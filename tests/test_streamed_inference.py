"""Inference's memory, and inference streamed by folder.

What a claim costs: a defeater's reason is its rule's sentence, rendered. A pass renders each
sentence once and hands every claim whose defeater did not fire the same record. Rendering it again
for every claim naming the rule would make a pass hold as many copies of a few sentences, several
hundred characters each, as it had defeaters.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from fixtures.laboratory_pool import write_pool
from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.core.schema import Claim, RuleEffect
from ingence.core.tiers import ResolutionStatus
from ingence.infer.claims import SharedDefeaters, claim_predicate, defeater_for
from ingence.infer.evaluate import Outcome
from ingence.rulebook import shipped_rulebook
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.render import render_rule
from ingence.scan.census import run_census

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000001Z_444444444444"
WHOLE = "run_20260101T000002Z_222222222222"
STREAMED = "run_20260101T000003Z_333333333333"


def _a_rule(effect: RuleEffect = RuleEffect.CLASSIFIES):
    load_builtins()
    return next(rule for rule in shipped_rulebook().rules if rule.effect is effect)


def test_a_defeater_that_did_not_fire_is_one_record_shared_by_every_claim_naming_it() -> None:
    rule = _a_rule()
    shared = SharedDefeaters()
    first = shared.defeater(rule, None)
    second = shared.defeater(rule, None)
    assert first is second
    assert first == defeater_for(rule, None)
    assert first.reason == render_rule(rule)


def test_a_fired_defeater_keeps_its_own_records_and_shares_only_the_sentence() -> None:
    rule = _a_rule()
    shared = SharedDefeaters()
    fired = Outcome(
        rule_id=rule.rule_id,
        subject="entry_kelt",
        status=ResolutionStatus.RESOLVED,
        values=("vorn",),
        inputs=("binding_zib",),
    )
    held = shared.defeater(rule, fired)
    assert held == defeater_for(rule, fired)
    assert held.fired
    assert held.observations == ("binding_zib",)
    assert held.reason is shared.defeater(rule, None).reason
    assert shared.defeater(rule, fired) is not held


def test_every_claim_of_one_rule_shares_its_predicate_string() -> None:
    rule = _a_rule()
    assert claim_predicate(rule) is claim_predicate(rule.model_copy())


# ---------------------------------------------------------------------------
# What a pass holds: claims as read-side views, and nothing of a pass through compaction.
# ---------------------------------------------------------------------------


def _generated(tmp_path: Path) -> Project:
    """A generated laboratory pool (invented names) under revision 4, censused."""
    pool = write_pool(tmp_path / "pool")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool.root])
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    return project


def test_the_claims_a_pass_holds_once_written_are_views_of_them(tmp_path, monkeypatch) -> None:
    from ingence.infer import run as run_module

    project = _generated(tmp_path)
    held: list[object] = []
    checked = run_module.check_all

    def capture(snapshot):
        held.extend(snapshot.claims)
        return checked(snapshot)

    monkeypatch.setattr(run_module, "check_all", capture)
    run_module.run_inference(project, run_id=WHOLE, started_at="2026-01-01T00:00:02Z")
    assert held
    assert all(isinstance(claim, Claim) for claim in held)
    assert not any(type(claim) is Claim for claim in held)


def test_nothing_a_pass_assembled_is_alive_while_its_run_is_compacted(
    tmp_path, monkeypatch
) -> None:
    import gc

    from ingence.infer import run as run_module
    from ingence.infer.subjects import EntryRecords

    project = _generated(tmp_path)
    alive: list[int] = []
    compact = run_module.compact_run

    def counted(*args, **kwargs):
        gc.collect()
        alive.append(sum(1 for held in gc.get_objects() if isinstance(held, EntryRecords | Claim)))
        return compact(*args, **kwargs)

    monkeypatch.setattr(run_module, "compact_run", counted)
    run_module.run_inference(project, run_id=WHOLE, started_at="2026-01-01T00:00:02Z")
    assert alive == [0]


# ---------------------------------------------------------------------------
# Compaction sorts a large table in ranges, and writes what it wrote in one sort.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["entries", "components"])
def test_a_table_sorted_in_ranges_is_the_table_sorted_whole(tmp_path, monkeypatch, table) -> None:
    """Sorted in ranges of its first column, a table is the table sorted whole, row for row.

    Components sort on their entry first, so one first-column value holds several rows and a range
    bounded by a row count would cut it. The ranges are bounded by values, so none does. The
    fragments are read once whatever the number of ranges, because reading every fragment again for
    each range grows with the square of the table.
    """
    import duckdb
    import pyarrow.parquet as pq

    from ingence.storage import compact
    from ingence.storage.commit import run_path
    from ingence.storage.layout import TABLES

    project = _generated(tmp_path)
    spec = next(spec for spec in TABLES if spec.name == table)
    published = pq.read_table(run_path(project.path, CENSUS) / spec.directory)
    assert published.num_rows > 20
    rows = published.to_pylist()
    first = spec.sort_by[0]
    if table == "components":
        held = [row[first] for row in rows]
        assert len(set(held)) < len(held)  # some first-column value holds several rows

    scans: list[str] = []
    connect = duckdb.connect

    class Counting:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, *args, **kwargs):
            # A query reading every row of the fragments: not their footers, nor the first rows
            # a batch's size is measured on.
            if "$sources" in sql and not any(
                word in sql for word in ("DESCRIBE", "parquet_file_metadata", " LIMIT ")
            ):
                scans.append(sql)
            return self.inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(compact.duckdb, "connect", lambda *a, **k: Counting(connect(*a, **k)))

    def compacted(name: str, partition_rows: int | None) -> list[dict[str, object]]:
        directory = tmp_path / name
        directory.mkdir()
        # Seven fragments, each spanning the whole key range and out of order, as a pass's
        # writers leave them.
        for index in range(7):
            pq.write_table(
                pa.Table.from_pylist(rows[index::7][::-1], schema=published.schema),
                directory / f"part-{index:05d}.parquet",
            )
        scans.clear()
        assert compact.compact_table(directory, spec, partition_rows=partition_rows) == len(rows)
        assert sorted(path.name for path in directory.iterdir()) == [compact.COMPACTED_NAME]
        return pq.read_table(directory / compact.COMPACTED_NAME).to_pylist()

    whole = compacted("whole", len(rows) + 1)
    assert [row[first] for row in whole] == sorted(row[first] for row in rows)
    for partition_rows in (1, 2, 7):
        assert compacted(f"ranges-{partition_rows}", partition_rows) == whole
        # Many ranges: a sample of the first column, and one pass bucketing every row.
        assert len(scans) <= 2
    # A few ranges are read a range at a time: the sample, then each range and the nulls.
    few = -(-len(rows) // compact.MAXIMUM_REREADS)
    assert compacted("few", few) == whole
    assert 2 < len(scans) <= compact.MAXIMUM_REREADS + 2


# ---------------------------------------------------------------------------
# Streaming by folder: the same rows, a chunk of whole folders at a time.
# ---------------------------------------------------------------------------


def _with_infer(project: Project, **values: object) -> Project:
    import dataclasses

    from ingence.core.settings import InferSettings

    infer = InferSettings(**{**project.settings.infer.model_dump(), **values})
    return dataclasses.replace(
        project, settings=project.settings.model_copy(update={"infer": infer})
    )


def _dived(tmp_path: Path) -> Project:
    """The generated pool censused and dived, so its entries carry observations to stream."""
    from ingence.scan.deepen import run_deepen

    project = _generated(tmp_path)
    run_deepen(project, run_id=DIVE, started_at="2026-01-01T00:00:01Z", budget_seconds=60)
    return project


def test_every_registered_predicate_declares_its_reach() -> None:
    from ingence.infer.streaming import REACH, unclassified
    from ingence.rulebook.registry import PREDICATES

    load_builtins()
    assert unclassified(PREDICATES.names()) == ()
    # And the table names no predicate that is not registered, so a line never outlives its rule.
    assert set(REACH) <= set(PREDICATES.names())


def test_a_streamed_pass_publishes_every_row_the_whole_pass_publishes(tmp_path) -> None:
    import shutil

    from ingence.infer.run import run_inference
    from ingence.storage.catalog import compare_runs
    from ingence.storage.commit import run_path

    whole = _dived(tmp_path / "whole")
    streamed_path = tmp_path / "streamed"
    shutil.copytree(whole.path, streamed_path)
    streamed = _with_infer(Project.open(streamed_path), streaming="always", entries_per_chunk=3)

    first = run_inference(
        _with_infer(whole, streaming="never"), run_id=WHOLE, started_at="2026-01-01T00:00:02Z"
    )
    second = run_inference(streamed, run_id=STREAMED, started_at="2026-01-01T00:00:02Z")
    assert not first.streamed
    assert second.streamed
    assert second.streaming_reason == "infer.streaming is always"
    assert (first.claims, first.candidates, first.findings, first.failures) == (
        second.claims,
        second.candidates,
        second.findings,
        second.failures,
    )
    assert first.claims > 0
    assert first.findings > 0
    assert first.candidates > 0
    # The generated pool reaches every rule class the pass evaluates, so the two passes agreeing
    # is an agreement about each class, and not only about the rules that read one entry.
    from ingence.infer.streaming import reach_of
    from ingence.rulebook import project_rulebook

    rules = project_rulebook(whole.path).rules
    declared = {rule.rule_id: reach_of(rule.predicate) for rule in rules}
    reached = {
        str(declared[tally.rule_id])
        for tally in (*second.tallies, *second.groupings, *second.flags)
        if tally.evaluated
    } | {str(declared[tally.rule_id]) for tally in second.proposals if tally.pairs}
    assert reached >= {"entry", "attached", "folder", "root", "pool", "pair"}
    shutil.copytree(run_path(streamed_path, STREAMED), run_path(whole.path, STREAMED))
    assert compare_runs(whole.path, WHOLE, STREAMED) == {}


def test_a_streamed_pass_never_holds_every_entry_s_observations(tmp_path, monkeypatch) -> None:
    from ingence.infer import run as run_module

    project = _with_infer(_dived(tmp_path), streaming="always", entries_per_chunk=3)
    loaded: list[int] = []
    load = run_module.observations_of

    def counted(catalog, entry_ids):
        found = load(catalog, entry_ids)
        loaded.append(sum(len(held) for held in found.values()))
        return found

    monkeypatch.setattr(run_module, "observations_of", counted)
    assembled = run_module.assemble_entry_records
    held: list[int] = []

    def assembled_counted(*args, **kwargs):
        records = assembled(*args, **kwargs)
        held.append(sum(len(records_.observations) for records_ in records))
        return records

    monkeypatch.setattr(run_module, "assemble_entry_records", assembled_counted)
    run_module.run_inference(project, run_id=STREAMED, started_at="2026-01-01T00:00:02Z")
    assert held == [0]
    assert loaded
    assert max(loaded) < sum(loaded)


def test_a_rule_whose_reach_is_undeclared_runs_whole_and_the_run_says_why(
    tmp_path, monkeypatch
) -> None:
    from ingence.infer import streaming
    from ingence.infer.run import run_inference
    from ingence.storage.catalog import open_catalog

    project = _with_infer(_generated(tmp_path), streaming="always")
    declared = dict(streaming.REACH)
    declared.pop("earliest_credible_time")
    monkeypatch.setattr(streaming, "REACH", declared)
    report = run_inference(project, run_id=STREAMED, started_at="2026-01-01T00:00:02Z")
    assert not report.streamed
    assert "earliest_credible_time" in report.streaming_reason
    with open_catalog(project.path) as catalog:
        row = catalog.query("SELECT streamed, streaming_reason FROM run").fetchone()
    assert row == (False, report.streaming_reason)


def test_auto_streams_where_the_projected_peak_exceeds_the_machine_less_its_margin() -> None:
    from ingence.core.settings import InferSettings
    from ingence.infer.streaming import decide_streaming

    settings = InferSettings(
        streaming="auto", projected_bytes_per_entry=1000, memory_margin_fraction=0.25
    )
    over = decide_streaming(settings, entries=800, predicates=(), physical=1_000_000)
    under = decide_streaming(settings, entries=700, predicates=(), physical=1_000_000)
    unread = decide_streaming(settings, entries=800, predicates=(), physical=None)
    assert over.streamed
    assert not under.streamed
    assert not unread.streamed
    assert "could not be read" in unread.reason
    assert "800 entries" in over.reason
    never = decide_streaming(
        InferSettings(streaming="never"), entries=10**9, predicates=(), physical=1
    )
    assert not never.streamed
    # An undeclared reach is named where the pass would have streamed, and only there.
    fits = decide_streaming(settings, entries=700, predicates=("kelt_vorn",), physical=1_000_000)
    refused = decide_streaming(settings, entries=800, predicates=("kelt_vorn",), physical=1_000_000)
    assert not fits.streamed
    assert "kelt_vorn" not in fits.reason
    assert not refused.streamed
    assert "kelt_vorn" in refused.reason
    assert "800 entries" in refused.reason


def test_a_chunk_holds_whole_folders_and_no_more_than_its_bound_but_for_one_folder() -> None:
    from ingence.infer.streaming import folder_chunks

    def records(tmp: Path) -> tuple:
        from ingence.infer.records import assemble_entry_records
        from ingence.rulebook.registry import Reads
        from ingence.storage.catalog import open_catalog

        project = _generated(tmp)
        with open_catalog(project.path) as catalog:
            return assemble_entry_records(catalog, reads={Reads.ENTRY})

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        held = records(Path(tmp))
    for bound in (1, 3, 10, len(held)):
        chunks = folder_chunks(held, bound)
        assert sorted(position for chunk in chunks for position in chunk) == list(range(len(held)))
        chunk_of = {position: number for number, chunk in enumerate(chunks) for position in chunk}
        by_parent: dict[object, set[int]] = {}
        for position, records_ in enumerate(held):
            by_parent.setdefault(records_.entry.parent_entry_id, set()).add(chunk_of[position])
        assert all(len(numbers) == 1 for numbers in by_parent.values())
        largest = max(len(members) for members in _listings(held).values())
        assert all(len(chunk) <= max(bound, largest) for chunk in chunks)


def _listings(held) -> dict[object, list[int]]:
    listed: dict[object, list[int]] = {}
    for position, records_ in enumerate(held):
        listed.setdefault(records_.entry.parent_entry_id, []).append(position)
    return listed


def test_the_infer_verb_says_how_the_pass_held_the_pool(tmp_path) -> None:
    import os
    import subprocess
    import sys

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn_01.csv").write_text("a,b\n1,2\n")
    project = tmp_path / "project"
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "ingence.cli.main", *args],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    assert run("init", str(project), "--root", str(pool)).returncode == 0
    assert run("index", "-p", str(project), "-q").returncode == 0
    inferred = run("infer", "-p", str(project), "--verbose")
    assert inferred.returncode == 0, inferred.stderr
    # Streaming is off unless a person turns it on. How the pass held the pool is the developer's
    # detail, printed with --verbose.
    said = " ".join(inferred.stdout.split())
    assert "held the whole pool at once: infer.streaming is never" in said


# ---------------------------------------------------------------------------
# The previous run's claims and edges, read by Arrow batch.
# ---------------------------------------------------------------------------


def test_the_carried_claims_and_edges_are_read_by_batch_and_held_as_views(
    tmp_path, monkeypatch
) -> None:
    import shutil

    from ingence.core.schema import Assertion
    from ingence.infer import records as records_module
    from ingence.infer import run as run_module
    from ingence.storage.catalog import compare_runs
    from ingence.storage.commit import run_path

    whole = _generated(tmp_path / "whole")
    batched_path = tmp_path / "batched"
    shutil.copytree(whole.path, batched_path)
    run_module.run_inference(whole, run_id=WHOLE, started_at="2026-01-01T00:00:02Z")

    batches: list[int] = []
    reader = records_module.batched_rows

    def counted(catalog, sql, parameters=None):
        for row in reader(catalog, sql, parameters):
            batches.append(1)
            yield row

    edges: list[object] = []
    checked = run_module.check_all

    def capture(snapshot):
        edges.extend(snapshot.assertions)
        return checked(snapshot)

    monkeypatch.setattr(records_module, "ROWS_PER_BATCH", 1)
    monkeypatch.setattr(run_module, "batched_rows", counted)
    monkeypatch.setattr(run_module, "check_all", capture)
    run_module.run_inference(
        Project.open(batched_path), run_id=STREAMED, started_at="2026-01-01T00:00:02Z"
    )
    assert batches
    assert edges
    assert all(isinstance(edge, Assertion) and type(edge) is not Assertion for edge in edges)
    shutil.copytree(run_path(batched_path, STREAMED), run_path(whole.path, STREAMED))
    assert compare_runs(whole.path, WHOLE, STREAMED) == {}


def test_each_reading_of_the_pool_starts_where_the_last_one_finished() -> None:
    from ingence.infer.streaming import ChunkedObservations

    asked: list[tuple[str, ...]] = []

    def load(entry_ids):
        asked.append(tuple(entry_ids))
        return {}

    one = ChunkedObservations(((0, 1),), load, ids=("kelt", "vorn"))
    for _ in range(3):
        one.observations(one.order()[0], ("kelt", "vorn"))
    assert one.loads == 1
    three = ChunkedObservations(((0,), (1,), (2,)), load, ids=("zib0", "zib1", "zib2"))
    readings = [three.order() for _ in range(3)]
    assert readings == [[0, 1, 2], [2, 1, 0], [0, 1, 2]]
    for reading in readings:
        for index in reading:
            three.observations(index, (f"zib{index}",))
    assert three.loads == 7


# ---------------------------------------------------------------------------
# Assembly orders each entry's records itself, rather than asking DuckDB to sort whole tables.
# ---------------------------------------------------------------------------


def test_records_ordered_per_entry_are_the_records_a_whole_table_sort_gave(
    tmp_path, monkeypatch
) -> None:
    from ingence.core.schema import Component, DictionaryBinding, Entry, Observation
    from ingence.infer import records as records_module
    from ingence.rulebook.registry import Reads
    from ingence.storage.catalog import open_catalog
    from ingence.storage.views import view_from_row

    project = _dived(tmp_path)
    sorts: list[str] = []
    reader = records_module.batched_rows

    def watched(catalog, sql, parameters=None):
        if "ORDER BY" in sql and " FROM roots " not in f"{sql} ":
            sorts.append(sql)
        return reader(catalog, sql, parameters)

    monkeypatch.setattr(records_module, "ROWS_PER_BATCH", 5)
    monkeypatch.setattr(records_module, "batched_rows", watched)
    with open_catalog(project.path) as catalog:
        held = records_module.assemble_entry_records(catalog, reads={Reads.RECORDS})

        # The reference: every table sorted whole by DuckDB.
        def table(name, key, record):
            grouped: dict[str, list[object]] = {}
            for row in reader(catalog, f"SELECT * FROM {name} ORDER BY entry_id, {key}"):
                grouped.setdefault(str(row["entry_id"]), []).append(view_from_row(row, record))
            return grouped

        entries = [
            view_from_row(row, Entry)
            for row in reader(
                catalog, "SELECT * FROM entries WHERE NOT is_deleted ORDER BY entry_id"
            )
        ]
        components = table("components", "component_id", Component)
        bindings = table("dictionary_bindings", "binding_id", DictionaryBinding)
        observations = table("observations", "observation_id", Observation)
    assert sorts == []
    assert [records.entry for records in held] == entries
    assert sum(len(records.observations) for records in held) > 0
    for records in held:
        entry_id = records.entry.entry_id
        assert list(records.components) == components.get(entry_id, [])
        assert list(records.bindings) == bindings.get(entry_id, [])
        assert list(records.observations) == observations.get(entry_id, [])


def test_a_predicate_may_declare_its_reach_where_it_is_registered(monkeypatch) -> None:
    """A new predicate states its reach in its own registration, or in the table."""
    from ingence.core.registry import Registry
    from ingence.infer import streaming
    from ingence.rulebook import registry as registry_module
    from ingence.rulebook.registry import Reads, declare

    fresh: Registry = Registry("predicate")
    monkeypatch.setattr(registry_module, "PREDICATES", fresh)
    monkeypatch.setattr(streaming, "PREDICATES", fresh)

    @declare(
        "kelt_within_vorn",
        version="1",
        summary="an invented grouping reading one entry's records",
        effect=RuleEffect.GROUPS,
        reads=Reads.RECORDS,
        reach="entry",
    )
    def _kelt() -> object:  # pragma: no cover - never built
        return object()

    @declare(
        "zib_undeclared",
        version="1",
        summary="an invented predicate declaring no reach",
        effect=RuleEffect.CLASSIFIES,
        reads=Reads.ENTRY,
    )
    def _zib() -> object:  # pragma: no cover - never built
        return object()

    assert streaming.reach_of("kelt_within_vorn") is streaming.Reach.ENTRY
    assert streaming.reach_of("zib_undeclared") is None
    assert streaming.unclassified(["kelt_within_vorn", "zib_undeclared"]) == ("zib_undeclared",)
    with pytest.raises(ValueError, match="brisa"):

        @declare(
            "trell_misdeclared",
            version="1",
            summary="an invented predicate declaring a reach that does not exist",
            effect=RuleEffect.CLASSIFIES,
            reads=Reads.ENTRY,
            reach="brisa",
        )
        def _trell() -> object:  # pragma: no cover - never built
            return object()


def test_the_machine_s_memory_is_the_least_of_its_own_and_its_container_s(tmp_path) -> None:
    """In a Linux container the process has its cgroup's memory, not its host's."""
    from ingence.infer.streaming import physical_memory

    gib = 1024**3
    # No cgroup files at all: the machine's own.
    host = physical_memory(cgroup_root=tmp_path / "none")
    assert host is not None and host > 0
    # Version 2 with a limit, and with none.
    two = tmp_path / "two"
    two.mkdir()
    (two / "memory.max").write_text(f"{2 * gib}\n")
    assert physical_memory(cgroup_root=two) == min(host, 2 * gib)
    (two / "memory.max").write_text("max\n")
    assert physical_memory(cgroup_root=two) == host
    # Version 1 with a limit, and with the huge value it writes for none.
    one = tmp_path / "one"
    (one / "memory").mkdir(parents=True)
    (one / "memory" / "memory.limit_in_bytes").write_text(f"{3 * gib}\n")
    assert physical_memory(cgroup_root=one) == min(host, 3 * gib)
    # The kernel's page counter maximum times a 4 KiB page, which version 1 writes for no limit.
    (one / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712\n")
    assert physical_memory(cgroup_root=one) == host


def test_scope_rules_are_handed_the_same_records_streamed_or_whole(tmp_path, monkeypatch) -> None:
    """A region's records hold no observations in either pass, so a scope rule reads the same."""
    from ingence.infer import run as run_module

    handed: list[int] = []
    assemble = run_module.assemble_scope_records

    def counted(records, **kwargs):
        handed.append(sum(len(held.observations) for held in records))
        return assemble(records, **kwargs)

    outcomes = run_module._flag_outcomes

    def watched(rule, predicate, subjects, scopes, **kwargs):
        holders = kwargs.get("holders") or {}
        handed.append(sum(len(held.observations) for held in holders.values()))
        yield from outcomes(rule, predicate, subjects, scopes, **kwargs)

    monkeypatch.setattr(run_module, "assemble_scope_records", counted)
    monkeypatch.setattr(run_module, "_flag_outcomes", watched)
    project = _with_infer(_dived(tmp_path), streaming="never")
    run_module.run_inference(project, run_id=WHOLE, started_at="2026-01-01T00:00:02Z")
    assert handed
    assert set(handed) == {0}


def test_a_scope_rule_naming_a_defeater_that_reads_observations_runs_whole(
    tmp_path, monkeypatch
) -> None:
    from ingence.infer import run as run_module
    from ingence.rulebook.registry import Reads

    reads = run_module.reads_observations

    def scope_rules_observe(rule, shapes, in_force):
        return shapes.get(rule.rule_id) is Reads.SCOPE or reads(rule, shapes, in_force)

    monkeypatch.setattr(run_module, "reads_observations", scope_rules_observe)
    project = _with_infer(_generated(tmp_path), streaming="always")
    report = run_module.run_inference(project, run_id=STREAMED, started_at="2026-01-01T00:00:02Z")
    assert not report.streamed
    assert "reads a scope and names a defeater reading observations" in report.streaming_reason
    assert "installed_software_in_folder" in report.streaming_reason


def test_a_grouping_rule_holds_no_member_s_observations_while_its_classes_form(
    tmp_path, monkeypatch
) -> None:
    """What a grouping rule keeps for each keyed entry until its classes form holds no observations.

    Kept whole, a streamed pass would hold every keyed entry's observations at once.
    """
    from ingence.infer import run as run_module
    from ingence.infer.subjects import EntryRecords

    seen: list[int] = []
    classes = run_module.classes_of

    def watched(keyed):
        for _, (held, _outcome) in keyed:
            if isinstance(held, EntryRecords):
                seen.append(len(held.observations))
        return classes(keyed)

    monkeypatch.setattr(run_module, "classes_of", watched)
    project = _with_infer(_dived(tmp_path), streaming="always", entries_per_chunk=3)
    run_module.run_inference(project, run_id=STREAMED, started_at="2026-01-01T00:00:02Z")
    assert seen
    assert set(seen) == {0}


def test_a_streamed_pass_that_fails_closes_the_catalog_it_loads_chunks_from(
    tmp_path, monkeypatch
) -> None:
    import contextlib

    from ingence.infer import run as run_module

    opened: list[str] = []
    real = run_module.open_catalog

    @contextlib.contextmanager
    def counted(*args, **kwargs):
        opened.append("open")
        try:
            with real(*args, **kwargs) as catalog:
                yield catalog
        finally:
            opened.append("closed")

    def failing(*args, **kwargs):
        message = "a proposing phase that fails"
        raise RuntimeError(message)

    monkeypatch.setattr(run_module, "open_catalog", counted)
    monkeypatch.setattr(run_module, "_propose", failing)
    project = _with_infer(_dived(tmp_path), streaming="always", entries_per_chunk=3)
    try:
        run_module.run_inference(project, run_id=STREAMED, started_at="2026-01-01T00:00:02Z")
    except RuntimeError:
        # Asked while the failure is in hand, so its frames are alive: closed by the pass itself,
        # never by a collector reclaiming them later.
        assert opened.count("open") == opened.count("closed") > 0
    else:  # pragma: no cover - the phase always fails here
        pytest.fail("the pass did not fail")


def test_chunks_cover_every_subject_once_and_refuse_subjects_out_of_line() -> None:
    """The chunks and the subjects handed through them must agree entry for entry."""
    from ingence.core.schema import Entry, EntryType
    from ingence.infer.streaming import ChunkedObservations, ChunkedSubjects
    from ingence.infer.subjects import EntryRecords

    def load(entry_ids):
        return {}

    with pytest.raises(ValueError, match="more than one chunk"):
        ChunkedObservations(((0, 1), (1,)), load, ids=("kelt", "vorn", "zib"))
    with pytest.raises(ValueError, match="no entry"):
        ChunkedObservations(((0, 5),), load, ids=("kelt", "vorn"))

    def held(name: str) -> EntryRecords:
        return EntryRecords(
            entry=Entry.model_construct(
                entry_id=name, entry_type=EntryType.FILE, name=name, relative_path=name
            )
        )

    subjects = (held("kelt"), held("vorn"), held("zib"))
    chunks = ChunkedObservations(((2,),), load, ids=("kelt", "vorn", "zib"))
    seen = [records.entry.entry_id for records in ChunkedSubjects(subjects, chunks)]
    assert sorted(seen) == ["kelt", "vorn", "zib"]
    with pytest.raises(ValueError, match="line up"):
        list(ChunkedSubjects((subjects[1], subjects[0], subjects[2]), chunks))
    with pytest.raises(ValueError, match="line up"):
        list(ChunkedSubjects(subjects[:2], chunks))
