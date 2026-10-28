"""The catalog resolves through the pointer and answers the reuse question."""

from __future__ import annotations

import pytest

from ingence.core.project import Project
from ingence.core.schema import ScanOutcome
from ingence.scan.census import run_census
from ingence.storage.catalog import VOLATILE_COLUMNS, compare_runs, open_catalog
from ingence.storage.layout import TABLES


def project_with_pool(tmp_path):
    pool = tmp_path / "pool"
    (pool / "a").mkdir(parents=True)
    (pool / "a" / "one.txt").write_text("one")
    (pool / "two.csv").write_text("two")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool]), pool


def run(project, suffix: str):
    return run_census(project, run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}", started_at="t")


def test_an_empty_project_reads_as_empty_rather_than_failing(tmp_path):
    """Every table must be queryable before anything has ever been written."""
    project, _ = project_with_pool(tmp_path)
    with open_catalog(project.path) as catalog:
        assert not catalog.has_run
        for spec in TABLES:
            assert catalog.count(spec.name) == 0


def test_the_catalog_resolves_through_the_pointer(tmp_path):
    project, _ = project_with_pool(tmp_path)
    first = run(project, "1")
    second = run(project, "2")
    with open_catalog(project.path) as catalog:
        assert catalog.run_directory is not None
        assert catalog.run_directory.name == second.run_id
    with open_catalog(project.path, run_id=first.run_id) as catalog:
        assert catalog.run_directory is not None
        assert catalog.run_directory.name == first.run_id


def test_time_travel_reads_a_superseded_run(tmp_path):
    """Previous runs stay queryable, which is what makes the graph a function."""
    project, pool = project_with_pool(tmp_path)
    first = run(project, "1")
    (pool / "three.md").write_text("three")
    run(project, "2")
    with open_catalog(project.path, run_id=first.run_id) as catalog:
        assert catalog.count("entries") == first.counts.entries


def test_previous_records_answer_what_reuse_needs_to_know(tmp_path):
    project, _ = project_with_pool(tmp_path)
    run(project, "1")
    declaration = project.identity.roots[0]
    with open_catalog(project.path) as catalog:
        previous = catalog.previous_records(declaration.root_id)
    assert previous
    record = next(iter(previous.values()))
    assert record.stat_fingerprint
    assert record.processing_fingerprint
    assert record.scan_outcome is ScanOutcome.METADATA_ONLY
    assert record.first_seen_run
    # Nothing has been hashed, so both flags are false. Correct, not convenient.
    assert not record.has_full_hash
    assert not record.has_quick_hash


def test_a_tombstoned_entry_is_not_offered_for_reuse(tmp_path):
    """Reusing a record that describes an absence would be wrong."""
    project, pool = project_with_pool(tmp_path)
    run(project, "1")
    (pool / "two.csv").unlink()
    run(project, "2")
    declaration = project.identity.roots[0]
    with open_catalog(project.path) as catalog:
        previous = catalog.previous_records(declaration.root_id)
        live = catalog.live_entry_ids(declaration.root_id)
    assert len(previous) == len(live)


def test_a_missing_run_is_reported_clearly(tmp_path):
    project, _ = project_with_pool(tmp_path)
    with (
        pytest.raises(FileNotFoundError, match="does not exist"),
        open_catalog(project.path, run_id="run_absent"),
    ):
        pass


def test_the_volatile_column_list_is_the_reproducibility_claim():
    """Adding a column here quietly weakens the claim, so it is asserted."""
    assert (
        frozenset(
            {
                "run_id",
                "first_seen_run",
                "last_seen_run",
                "against_run_id",
                "parent_run_id",
                "observed_at",
                "occurred_at",
                "started_at",
                "ended_at",
                "elapsed_ns",
                "duration_ns",
                # The engine's own peak memory at the end of a run: a measure of the machine and
                # the moment, held by the quality figures and by nothing that describes the data.
                "peak_bytes",
                "phase",
                # The two columns describing the data, each with its reason: a filesystem such as
                # ExFAT can give a file a new inode, and a remount gives a network share a new
                # device number.
                "inode",
                "device",
                # A share's owner and group as the mounting machine maps them: a walk from another
                # laptop differs on every entry and nowhere else.
                "uid",
                "gid",
            }
        )
        == VOLATILE_COLUMNS
    )


def _rewrite_run(project, run_id: str, rewrite) -> None:
    """Rewrite every string and integer column of one run's tables in place, for a test."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ingence.storage.commit import run_path

    for path in sorted(run_path(project.path, run_id).rglob("*.parquet")):
        table = pq.read_table(path)
        columns = [rewrite(field.name, table.column(field.name)) for field in table.schema]
        pq.write_table(pa.table(columns, schema=table.schema), path)


def test_an_inode_the_filesystem_changed_is_not_a_reproducibility_failure(tmp_path):
    """macOS's ExFAT driver hands an empty file a new inode from one lookup to the next."""
    import pyarrow as pa
    import pyarrow.compute as pc

    project, _ = project_with_pool(tmp_path)
    first = run(project, "1")
    second = run(project, "2")

    def new_inodes(name, column):
        if name != "inode":
            return column
        return pc.add(column.cast(pa.uint64()), pa.scalar(1, pa.uint64())).cast(column.type)

    _rewrite_run(project, second.run_id, new_inodes)
    assert compare_runs(project.path, first.run_id, second.run_id) == {}


def test_a_remount_that_renumbered_the_device_is_not_a_reproducibility_failure(tmp_path):
    """A network share is given a new device number each time it is mounted."""
    import pyarrow as pa
    import pyarrow.compute as pc

    project, _ = project_with_pool(tmp_path)
    first = run(project, "1")
    second = run(project, "2")

    def remounted(name, column):
        if name != "device":
            return column
        return pc.add(column.cast(pa.int64()), pa.scalar(2, pa.int64())).cast(column.type)

    _rewrite_run(project, second.run_id, remounted)
    assert compare_runs(project.path, first.run_id, second.run_id) == {}


def test_a_comparison_sees_through_a_schema_bump(tmp_path):
    """A bump rewrote the version inside every serialised nested record."""
    import pyarrow as pa
    import pyarrow.compute as pc

    from ingence import SCHEMA_VERSION

    project, _ = project_with_pool(tmp_path)
    first = run(project, "1")
    second = run(project, "2")
    older = "0.99"

    def downgraded(name, column):
        if not pa.types.is_string(column.type):
            return column
        stamped = pc.replace_substring(
            column, f'"schema_version":"{SCHEMA_VERSION}"', f'"schema_version":"{older}"'
        )
        return (
            pc.if_else(pc.equal(stamped, SCHEMA_VERSION), older, stamped)
            if name == "schema_version"
            else stamped
        )

    _rewrite_run(project, second.run_id, downgraded)
    assert compare_runs(project.path, first.run_id, second.run_id) == {}

    # A real change still shows: a column's own value, not its version stamp.
    def renamed(name, column):
        if name != "name":
            return column
        return pc.utf8_upper(column)

    _rewrite_run(project, second.run_id, renamed)
    assert compare_runs(project.path, first.run_id, second.run_id)


def test_comparison_is_symmetric(tmp_path):
    """Counting only one direction reports nothing when the other side has more."""
    project, pool = project_with_pool(tmp_path)
    first = run(project, "1")
    (pool / "extra.txt").write_text("extra")
    second = run(project, "2")
    forward = compare_runs(project.path, first.run_id, second.run_id)
    backward = compare_runs(project.path, second.run_id, first.run_id)
    assert forward == backward
    assert forward


def test_the_two_unbound_denominators_are_reported_separately(tmp_path):
    """The word denominator and the all-kinds figure are reported separately, each with a rate.

    A corpus analysis reports stem word occurrences with the suffix removed
    before parsing, and separately an all-atom-kinds figure. A report needs a
    rate from the Index, because a ranked list of unbound tokens with no rate
    gives a number that cannot be reproduced from it.

    The engine reaches the same word denominator by a different route. It
    splits the extension off into a component of its own kind before anything is
    tokenised, so a stem is what remains without anybody removing a suffix. The
    all-kinds figure is necessarily the larger of the two, because it also counts
    dates, numbers, versions and identifiers, which are structurally understood
    while intentionally lacking a binding.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "confocal_stack_2026-01-04_rep2.tif").write_text("x")
    (pool / "zzqqxx_wibble.csv").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")

    with open_catalog(project.path) as catalog:
        rates = catalog.unbound_rate()

    assert rates["word_occurrences"] > 0
    assert rates["atom_occurrences"] >= rates["word_occurrences"]
    assert 0.0 <= rates["word_unbound_rate"] <= 1.0
    assert rates["word_unbound"] <= rates["word_occurrences"]
    # The rate is a fraction of its own denominator, computed rather than stored,
    # so the two can never drift apart.
    assert rates["word_unbound_rate"] == rates["word_unbound"] / rates["word_occurrences"]


def test_the_shape_corpus_groups_by_skeleton_and_ranks_by_entries(tmp_path):
    """Aggregation over the pool, which is what shape coverage needs.

    Distinct from the per-folder composition signature: that one asks what
    mixture a folder holds, this one asks what shapes a pool uses. Same input,
    two questions, and both are wanted.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    for index in range(5):
        (pool / f"confocal_stack_{index}.tif").write_text("x")
    (pool / "notes.md").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")

    with open_catalog(project.path) as catalog:
        rows = catalog.shape_corpus(limit=10)

    assert rows, "shapes should have been recorded"
    counts = [row["entries"] for row in rows]
    assert counts == sorted(counts, reverse=True), "commonest first"
    assert rows[0]["entries"] >= 5
    # A skeleton carries no content by construction, which is what makes a
    # corpus of these safe to keep for a pool whose contents are not.
    assert "confocal" not in rows[0]["skeleton"]


def test_a_fragment_written_before_a_field_existed_still_compacts(tmp_path) -> None:
    """Every publish compacts, so this decides whether an older project can run at all.

    A field added compatibly leaves earlier fragments without its column. ``union_by_name``
    fills a column that some fragment has, but refuses a column that no fragment has. So
    compaction gives such a column its declared default. Without that, an engine that added a
    field could not dive, infer or publish a project written before the field existed, because
    compaction is not optional. The test is the minimal case: one fragment, one column short
    (``defeaters_json``), and no fragment holding it.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ingence.core.schema import Finding
    from ingence.storage.compact import compact_table
    from ingence.storage.index_store import read_table
    from ingence.storage.layout import table_for

    spec = table_for(Finding)
    missing = "defeaters_json"
    assert missing in spec.schema.names, "the field this test is about has moved"

    older = pa.schema([field for field in spec.schema if field.name != missing])
    row = {
        name: ("x" if pa.types.is_string(older.field(name).type) else None) for name in older.names
    }
    row["finding_id"] = "finding_0000000000000000000000000000"
    directory = tmp_path / "findings"
    directory.mkdir()
    pq.write_table(pa.Table.from_pylist([row], schema=older), directory / "0001.parquet")

    written = compact_table(directory, spec)

    assert written == 1
    table = read_table(directory, spec)
    assert table.num_rows == 1
    assert table.schema.names == spec.schema.names
    # **As the field's declared default, not as null**, because the column is declared
    # non-nullable and the default is what the absence means: a run that could not record a
    # defeater recorded none, which is what an empty list says. A nullable column would read
    # as null, and a non-nullable column whose field has no default is refused by name
    # rather than filled, because that would assert something about rows written before
    # anybody could have asked.
    assert table.column(missing).to_pylist() == ["[]"]


def test_a_required_column_no_fragment_holds_is_refused_by_name(tmp_path) -> None:
    """Unknown is not empty, so storage does not fill it in.

    Where the declared schema requires a column, no fragment has it, and the field has no
    default, there is nothing honest to read. The refusal names the column so that whoever
    migrates the project knows where it stopped, rather than meeting a query planner's error.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pytest

    from ingence.core.schema import Finding
    from ingence.storage.compact import CompactionRefusedError, compact_table
    from ingence.storage.layout import table_for

    spec = table_for(Finding)
    required = "finding_id"
    declared = spec.model.model_fields[required]
    assert declared.is_required(), "the field this test needs has acquired a default"
    older = pa.schema([field for field in spec.schema if field.name != required])
    row = {
        name: ("x" if pa.types.is_string(older.field(name).type) else None) for name in older.names
    }
    directory = tmp_path / "findings"
    directory.mkdir()
    pq.write_table(pa.Table.from_pylist([row], schema=older), directory / "0001.parquet")

    with pytest.raises(CompactionRefusedError, match=required):
        compact_table(directory, spec)
