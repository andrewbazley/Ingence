"""The bounded content pass: budgets, profiles, isolation of failure."""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, ScanOutcome
from ingence.core.settings import ExtractionSettings, HashMode, HashSettings, Settings, SpendOrder
from ingence.executors.supervisor import live_child_count
from ingence.scan.cancel import CancellationToken
from ingence.scan.census import run_census
from ingence.scan.deepen import Budget, deepen_entry, run_deepen, spend_order
from ingence.scan.profiles import PROFILES, ProfileName, apply_profile
from ingence.storage.catalog import compare_runs, describe_differences, open_catalog


def build_pool(base):
    pool = base / "pool"
    (pool / "exp").mkdir(parents=True)
    (pool / "exp" / "results.csv").write_text("sample,treatment,dose\n1,a,5\n")
    (pool / "exp" / "mislabelled.txt").write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x11" * 2000
    )
    (pool / "notes.md").write_text("# Notes\n\nSome prose here.\n" * 20)
    return pool


def project_with_pool(tmp_path):
    pool = build_pool(tmp_path)
    return Project.create(tmp_path / "project", created_at="t", roots=[pool]), pool


def entry_for(path, name: str) -> Entry:
    return Entry(
        entry_id=f"entry_{name}",
        root_id="root_x",
        relative_path=name,
        name=name,
        entry_type=EntryType.FILE,
        extension=name.rsplit(".", 1)[-1],
        size_bytes=path.stat().st_size,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


# ---------------------------------------------------------------------------
# Profiles.
# ---------------------------------------------------------------------------


def test_census_is_the_only_profile_that_never_opens_a_file():
    opening = {name for name, profile in PROFILES.items() if profile.opens_files}
    assert ProfileName.CENSUS not in opening
    assert opening == {ProfileName.STANDARD, ProfileName.DEEP, ProfileName.CLUSTER}


def test_applying_a_profile_returns_settings_rather_than_mutating_them():
    """A preset is a starting position, not a silent permanent change.

    Where the settings leave the digest to the depth (``need_from="depth"``), the profile chooses
    it. A mode a person set is never replaced (``test_dive_by_evidence``).
    """
    original = Settings(hash=HashSettings(need_from="depth"))
    deep = apply_profile(original, ProfileName.DEEP)
    assert original.hash.mode is HashMode.NEED
    assert deep.hash.mode is HashMode.ADAPTIVE


def test_only_deep_hashes_across_a_network_share():
    """Hashing across a share is slow and hammers it for everyone else on it."""
    standard = apply_profile(Settings(), ProfileName.STANDARD)
    deep = apply_profile(Settings(), ProfileName.DEEP)
    assert standard.hash.skip_remote_filesystems
    assert not deep.hash.skip_remote_filesystems


# ---------------------------------------------------------------------------
# The budget.
# ---------------------------------------------------------------------------


def test_an_ungranted_budget_never_exhausts():
    budget = Budget(granted_seconds=None)
    budget.spend(10_000.0)
    assert not budget.exhausted


def test_a_granted_budget_exhausts_and_says_so():
    budget = Budget(granted_seconds=1.0)
    assert not budget.exhausted
    budget.spend(1.5)
    assert budget.exhausted


def _sized(files: list[tuple[str, int]]) -> list[Entry]:
    return [
        Entry(
            entry_id=f"entry_{index}",
            root_id="r",
            relative_path=name,
            name=name,
            extension=name.rsplit(".", 1)[-1] if "." in name else "",
            size_bytes=size,
            first_seen_run="r",
            last_seen_run="r",
        )
        for index, (name, size) in enumerate(files)
    ]


def test_smallest_first_is_still_there_to_be_measured_against(tmp_path):
    """A fixed budget spent on small files examines the most entries."""
    from ingence.core.settings import SpendOrder

    entries = _sized([("a.csv", 500), ("b.csv", 10), ("c.nd2", 900), ("d.txt", 10)])
    ordered = spend_order(entries, SpendOrder.SMALLEST_FIRST)
    assert [entry.size_bytes for entry in ordered] == [10, 10, 500, 900]
    # Canonical within a size, so two identical runs spend it identically.
    assert [entry.entry_id for entry in ordered[:2]] == ["entry_1", "entry_3"]


def test_every_family_gets_its_turn_before_any_family_gets_its_second(tmp_path):
    """Every family gets its turn before any family gets its second.

    Spent smallest first, a pool of many small files can reach no instrument file within two
    budgets."""
    entries = _sized(
        [
            ("a.csv", 3),
            ("b.csv", 1),
            ("c.csv", 2),
            ("big.nd2", 9_000),
            ("bigger.nd2", 90_000),
            ("notes.txt", 5),
            ("README", 4),
        ]
    )
    ordered = spend_order(entries)
    assert [entry.name for entry in ordered] == [
        # Each family's smallest, smallest first within the turn.
        "b.csv",
        "README",
        "notes.txt",
        "big.nd2",
        # Then each family's second.
        "c.csv",
        "bigger.nd2",
        # Then the one family with a third.
        "a.csv",
    ]
    # A function of the entries alone: shuffled input, the same order.
    assert spend_order(list(reversed(entries))) == ordered


# ---------------------------------------------------------------------------
# Examining one entry.
# ---------------------------------------------------------------------------


def test_examining_a_file_records_routing_identity_and_byte_character(tmp_path):
    pool = build_pool(tmp_path)
    path = pool / "exp" / "results.csv"
    outcome = deepen_entry(
        entry_for(path, "results.csv"),
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(
            extraction=ExtractionSettings(enabled=True), hash=HashSettings(mode=HashMode.FULL)
        ),
    )
    keys = {observation.key for observation in outcome.observations}
    assert "type.media_type" in keys
    assert "bytes.character" in keys
    assert "identity.blake3_whole" in keys
    assert not outcome.errors


def test_an_entry_s_stages_never_sum_to_more_than_the_entry_on_a_coarse_clock(
    tmp_path, monkeypatch
):
    """An entry's stages never sum to more than the entry, on a clock that ticks in 16 ms steps.

    Windows's clock ticks this way. A span taken through float seconds and back would fail:
    ``int(0.016 * 1e9)`` is 15,999,999, a nanosecond below the stage it contains. The entry is timed
    in whole nanoseconds on the stages' own clock, so the stages of an entry sum to no more than the
    entry however the clock ticks.
    """
    import itertools

    from ingence.scan import deepen as deepen_module

    ticks = itertools.count()

    def coarse() -> int:
        return next(ticks) * 16_000_000

    monkeypatch.setattr(deepen_module.time, "monotonic_ns", coarse)
    monkeypatch.setattr(deepen_module.time, "monotonic", lambda: coarse() / 1e9)
    pool = build_pool(tmp_path)
    path = pool / "exp" / "results.csv"
    outcome = deepen_entry(
        entry_for(path, "results.csv"),
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    monkeypatch.undo()
    assert outcome.stages
    assert sum(stage.duration_ns for stage in outcome.stages) <= outcome.nanoseconds
    assert outcome.nanoseconds % 16_000_000 == 0


def test_every_detector_opinion_is_recorded_not_only_the_winner(tmp_path):
    pool = build_pool(tmp_path)
    path = pool / "exp" / "mislabelled.txt"
    outcome = deepen_entry(
        entry_for(path, "mislabelled.txt"),
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(),
    )
    opinions = {
        observation.key
        for observation in outcome.observations
        if observation.key.startswith("type.opinion.")
    }
    assert len(opinions) >= 2


def test_a_misnamed_file_is_reported_as_disagreeing(tmp_path):
    pool = build_pool(tmp_path)
    path = pool / "exp" / "mislabelled.txt"
    outcome = deepen_entry(
        entry_for(path, "mislabelled.txt"),
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(),
    )
    agrees = next(
        observation
        for observation in outcome.observations
        if observation.key == "type.extension_agrees"
    )
    assert agrees.value_boolean is False


def test_an_unreadable_file_is_recorded_and_does_not_raise(tmp_path):
    """One unreadable file must never fail a pass."""
    missing = tmp_path / "absent.bin"
    entry = Entry(
        entry_id="entry_absent",
        root_id="r",
        relative_path="absent.bin",
        name="absent.bin",
        entry_type=EntryType.FILE,
        size_bytes=10,
        first_seen_run="r",
        last_seen_run="r",
    )
    outcome = deepen_entry(entry, missing, run_id="run_1", observed_at="t", settings=Settings())
    # Not reached: its stat failed before anything was read.
    assert outcome.outcome is ScanOutcome.DEFERRED


def test_observations_carry_the_tier_saying_how_far_from_the_bytes_they_sit(tmp_path):
    pool = build_pool(tmp_path)
    path = pool / "exp" / "results.csv"
    outcome = deepen_entry(
        entry_for(path, "results.csv"),
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(hash=HashSettings(mode=HashMode.FULL)),
    )
    by_key = {observation.key: observation for observation in outcome.observations}
    # An extension is a guess about a name; a signature is a reading of content.
    assert by_key["type.opinion.extension"].extraction_tier.value == "heuristic"
    assert by_key["identity.blake3_whole"].extraction_tier.value == "mechanically_exposed"


# ---------------------------------------------------------------------------
# A whole pass.
# ---------------------------------------------------------------------------


def run_index(project, suffix: str):
    return run_census(project, run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}", started_at="t")


def test_a_pass_publishes_a_new_run_carrying_everything_forward(tmp_path):
    project, _ = project_with_pool(tmp_path)
    census = run_index(project, "1")
    with open_catalog(project.path) as catalog:
        before = catalog.counts()

    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    assert report.examined == census.counts.files

    with open_catalog(project.path) as catalog:
        after = catalog.counts()
    assert after["entries"] == before["entries"]
    assert after["assertions"] == before["assertions"]
    assert after["claims"] == before["claims"]
    assert after["observations"] > before["observations"]


def test_an_explicit_grant_beats_the_profile_preset(tmp_path):
    """An option silently overridden by a default is worse than no option."""
    project, _ = project_with_pool(tmp_path)
    run_index(project, "1")
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        profile="standard",
        budget_seconds=7.0,
    )
    assert report.budget is not None
    assert report.budget.granted_seconds == 7.0


def test_an_exhausted_budget_leaves_a_complete_run_with_honest_gaps(tmp_path):
    """Running out of budget can never fail a run."""
    project, _ = project_with_pool(tmp_path)
    run_index(project, "1")
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=0.0,
    )
    assert report.examined == 0
    assert report.budget is not None
    assert report.budget.unreached > 0
    with open_catalog(project.path) as catalog:
        unexamined = catalog.query(
            "SELECT count(*) FROM entries WHERE scan_outcome = 'metadata_only'"
        ).fetchone()
        assert unexamined[0] > 0


def test_a_second_pass_continues_where_the_first_stopped(tmp_path):
    """Resumable by construction: each run records what it examined."""
    project, _ = project_with_pool(tmp_path)
    run_index(project, "1")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=0.0,
    )
    second = run_deepen(
        project,
        run_id="run_20260101T000003Z_333333333333",
        started_at="t",
        budget_seconds=60.0,
    )
    assert second.examined > 0


def test_deepening_before_indexing_is_refused(tmp_path):
    from ingence.storage.commit import CommitError

    project, _ = project_with_pool(tmp_path)
    with pytest.raises(CommitError, match="index before deepening"):
        run_deepen(project, run_id="run_x", started_at="t")


def test_the_run_record_states_which_components_were_active(tmp_path):
    """A run listing only what ran cannot be compared with a differently configured one."""
    project, _ = project_with_pool(tmp_path)
    run_index(project, "1")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    with open_catalog(project.path) as catalog:
        components = catalog.query("SELECT components_json FROM run").fetchone()
    assert components is not None
    assert "signature" in components[0]
    assert "blake3_whole" in components[0]


def test_a_dive_records_the_vocabulary_its_order_read(tmp_path):
    """The order depends on the Dictionary, so the run says which one.

    The extension rotation reads no vocabulary and claims none, and each examined entry's
    family is journalled, so where a file sat in the order can be read back from the run.
    """
    from ingence.dictionary.store import DictionaryStore

    project, _ = project_with_pool(tmp_path)
    run_index(project, "1")
    run_deepen(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        recorded = catalog.query("SELECT dictionary_hash FROM run").fetchone()
        families = catalog.query(
            "SELECT DISTINCT json_extract_string(detail, '$.family') FROM journal_events "
            "WHERE event = 'entry.examined'"
        ).fetchall()
    assert recorded == (DictionaryStore.for_project(project.path).fingerprint(),)
    assert {row[0] for row in families} >= {"tabular_format", "markdown"}

    project = project.save_settings(
        project.settings.model_copy(
            update={
                "extraction": project.settings.extraction.model_copy(
                    update={"spend_order": SpendOrder.EXTENSION_ROUND_ROBIN}
                )
            }
        )
    )
    run_deepen(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    with open_catalog(project.path) as catalog:
        assert catalog.query("SELECT dictionary_hash FROM run").fetchone() == (None,)


# ---------------------------------------------------------------------------
# The cascade, end to end.
# ---------------------------------------------------------------------------


def pool_with_containers(base):
    import gzip
    import io
    import zipfile

    pool = base / "pool"
    pool.mkdir(parents=True)
    with zipfile.ZipFile(pool / "experiment.zip", "w") as archive:
        archive.writestr("raw/acquisition.csv", "frame,intensity\n1,204\n2,199\n")
        archive.writestr("notes.txt", "Plate read at 620 nm.\n")
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as nested:
            nested.writestr("deep/summary.csv", "mean,sd\n201.5,3.5\n")
        archive.writestr("inner.zip", inner.getvalue())
    (pool / "logbook.txt.gz").write_bytes(gzip.compress(b"Session started.\n" * 5))
    return pool


def project_with_containers(tmp_path):
    pool = pool_with_containers(tmp_path)
    return Project.create(tmp_path / "project", created_at="t", roots=[pool]), pool


def test_container_members_become_entries_with_embedding_edges(tmp_path):
    """Making zipped content visible is the entire point of opening containers."""
    project, _ = project_with_containers(tmp_path)
    run_index(project, "1")
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    assert report.promoted >= 4

    with open_catalog(project.path) as catalog:
        derived = catalog.query(
            "SELECT count(*) FROM entries WHERE entry_kind = 'derived'"
        ).fetchone()[0]
        embeds = catalog.query(
            "SELECT count(*) FROM assertions WHERE primitive = 'embeds'"
        ).fetchone()[0]
    assert derived == embeds
    assert derived >= 4


def dive(project, suffix: str):
    return run_deepen(
        project,
        run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}",
        started_at="t",
        budget_seconds=60.0,
    )


def test_an_index_carries_the_members_of_containers_it_walked_so_a_dive_writes_one_row_each(
    tmp_path,
):
    """An index carries the members of the containers a dive opened.

    So a later dive writes one row for each member."""
    project, _ = project_with_containers(tmp_path)
    run_index(project, "1")
    dive(project, "2")
    with open_catalog(project.path) as catalog:
        members = set(
            catalog.query("SELECT entry_id FROM entries WHERE entry_kind = 'derived'").fetchall()
        )
    assert members

    report = run_index(project, "3")

    assert (report.counts.carried, report.counts.tombstones) == (len(members), 0)
    with open_catalog(project.path) as catalog:
        carried = set(
            catalog.query(
                "SELECT entry_id FROM entries WHERE entry_kind = 'derived' AND NOT is_deleted"
            ).fetchall()
        )
        embeds = catalog.query(
            "SELECT count(*) FROM assertions WHERE primitive = 'embeds'"
        ).fetchone()[0]
    assert carried == members
    assert embeds == len(members)

    dive(project, "4")
    with open_catalog(project.path) as catalog:
        repeated = catalog.query(
            "SELECT entry_id FROM entries GROUP BY entry_id HAVING count(*) > 1"
        ).fetchall()
        deleted = catalog.query("SELECT count(*) FROM entries WHERE is_deleted").fetchone()[0]
        promoted = set(
            catalog.query("SELECT entry_id FROM entries WHERE entry_kind = 'derived'").fetchall()
        )
    assert repeated == []
    assert deleted == 0
    assert promoted == members


def test_the_members_of_a_container_that_has_gone_are_tombstoned_keeping_what_they_were(
    tmp_path,
):
    project, pool = project_with_containers(tmp_path)
    run_index(project, "1")
    dive(project, "2")
    with open_catalog(project.path) as catalog:
        was = {
            row[0]: row[1:]
            for row in catalog.query(
                "SELECT entry_id, entry_kind, entry_type, relative_path, name, depth FROM entries"
            ).fetchall()
        }
    (pool / "experiment.zip").unlink()

    report = run_index(project, "3")

    with open_catalog(project.path) as catalog:
        gone = {
            row[0]: row[1:]
            for row in catalog.query(
                "SELECT entry_id, entry_kind, entry_type, relative_path, name, depth "
                "FROM entries WHERE is_deleted"
            ).fetchall()
        }
    assert gone == {entry_id: was[entry_id] for entry_id in gone}
    assert {row[0] for row in gone.values()} == {"filesystem", "derived"}
    assert report.counts.tombstones == len(gone)
    members = {entry_id for entry_id, row in was.items() if row[0] == "derived"}
    assert report.counts.carried == len(members - set(gone))


def test_every_embedding_edge_carries_an_expansion_state(tmp_path):
    """Without it the absence of a sibling member is unreadable."""
    project, _ = project_with_containers(tmp_path)
    run_index(project, "1")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    with open_catalog(project.path) as catalog:
        missing = catalog.query(
            "SELECT count(*) FROM assertions WHERE primitive = 'embeds' AND expansion_state IS NULL"
        ).fetchone()[0]
    assert missing == 0


def test_a_nested_container_is_opened_and_its_contents_read(tmp_path):
    """A member that is itself a container re-enters the pipeline."""
    project, _ = project_with_containers(tmp_path)
    run_index(project, "1")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, e.depth FROM entries e WHERE e.name = 'deep/summary.csv'"
        ).fetchall()
        assert rows, "the member two containers deep was never reached"
        assert rows[0][1] == 3

        columns = catalog.query(
            "SELECT o.value_structure FROM observations o "
            "JOIN entries e ON e.entry_id = o.entry_id "
            "WHERE e.name = 'deep/summary.csv' AND o.key = 'table.column_names'"
        ).fetchone()
    assert columns is not None
    assert "mean" in columns[0]


def test_a_deep_members_locator_chain_is_absolute(tmp_path):
    """A relative chain would only be meaningful to whoever produced it."""
    import json

    project, _ = project_with_containers(tmp_path)
    run_index(project, "1")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    with open_catalog(project.path) as catalog:
        chain = catalog.query(
            "SELECT locator_chain_json FROM entries WHERE name = 'deep/summary.csv'"
        ).fetchone()[0]
    steps = [step["target"] for step in json.loads(chain)]
    assert steps == ["inner.zip", "deep/summary.csv"]


def test_the_cascade_publishes_only_when_the_invariants_hold(tmp_path):
    """This pass draws edges, so it is held to the same publish check the census is."""
    project, _ = project_with_containers(tmp_path)
    run_index(project, "1")
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    assert report.invariants
    assert [o.invariant_id for o in report.invariants if not o.passed] == []


def test_promotion_can_be_switched_off_without_losing_the_evidence(tmp_path):
    """Turning it off loses the node, never the observation."""
    from ingence.core.settings import ExtractionSettings, Settings

    pool = pool_with_containers(tmp_path)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(
            extraction=ExtractionSettings(enabled=True, promote_container_members=False)
        ),
    )
    run_index(project, "1")
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    assert report.promoted == 0
    with open_catalog(project.path) as catalog:
        derived = catalog.query(
            "SELECT count(*) FROM entries WHERE entry_kind = 'derived'"
        ).fetchone()[0]
        recorded = catalog.query(
            "SELECT count(*) FROM observations WHERE key LIKE 'derived.%content_hash'"
        ).fetchone()[0]
    assert derived == 0
    assert recorded > 0


# ---------------------------------------------------------------------------
# Determinism, with the process boundary in the way.
# ---------------------------------------------------------------------------


def test_isolation_does_not_disturb_determinism(tmp_path):
    """Three deepening passes with a real child process, last two identical.

    This must hold before any work is parallelised, and the test asserts it. The census survives
    arriving in completion order because identifiers are content-derived and compaction sorts
    canonically. Extraction should inherit that protection, and inheriting it is not the same as
    having it.

    A container pool rather than plain files, because the container extractor is
    the one that declares ``Isolation.SUBPROCESS``: with plain files the
    boundary is never crossed and the test would pass without testing anything.
    """
    pool = pool_with_containers(tmp_path)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    assert project.settings.extraction.use_subprocess_isolation

    run_index(project, "1")
    identifiers = []
    for suffix in ("2", "3", "4"):
        run_deepen(
            project,
            run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}",
            started_at="t",
            budget_seconds=120.0,
        )
        identifiers.append(f"run_20260101T00000{suffix}Z_{suffix * 12}")

    differences = compare_runs(project.path, identifiers[1], identifiers[2])
    assert differences == {}, describe_differences(project.path, identifiers[1], identifiers[2])


def test_an_isolated_extractor_really_did_run_in_another_process(tmp_path):
    """Without this the determinism test above could pass by never isolating.

    Asserts the boundary was crossed rather than assuming the declaration was
    honoured: a component reported as active must have actually been able to run.
    """
    from ingence.extractors import load_builtins
    from ingence.extractors.base import EXTRACTORS, Isolation, isolation_of
    from ingence.scan.deepen import supervisor_for

    load_builtins()
    pool = pool_with_containers(tmp_path)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    run_index(project, "1")
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=120.0,
    )

    assert supervisor_for(project.settings) is not None
    assert isolation_of(EXTRACTORS.get("archive")) is Isolation.SUBPROCESS
    # Members came back across the boundary, which they cannot have done unless
    # the archive extractor ran and its result was marshalled home.
    assert report.promoted > 0


def test_a_cancelled_deepening_pass_publishes_nothing_and_leaves_no_children(tmp_path):
    """Cancellation during content work, which is the point the census never reaches.

    The census's five interruption points are all between directories, where a
    cooperative token is sufficient. This is the sixth and the one that needed a
    process boundary: a user who presses Ctrl-C while a parser is running must
    get their prompt back, and the earlier run must still be current.
    """
    from ingence.executors.supervisor import live_child_count
    from ingence.scan.cancel import CancellationToken
    from ingence.storage.commit import read_pointer

    pool = pool_with_containers(tmp_path)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    first = run_index(project, "1")

    token = CancellationToken()
    token.cancel("interrupted before content work began")
    run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=120.0,
        token=token,
    )

    assert live_child_count() == 0
    pointer = read_pointer(project.path)
    assert pointer is not None
    assert pointer.run_id == first.run_id


class _CancelsAfter(CancellationToken):
    """A token that cancels itself once the pass has examined ``after`` entries.

    Subclassed rather than driven from another thread because a race would make the test tell a
    different story on a loaded machine, and a test that passes intermittently is worse than no
    test.
    """

    def __init__(self, after: int) -> None:
        super().__init__()
        self._after = after
        self.reads = 0

    @property
    def cancelled(self) -> bool:
        if super().cancelled:
            return True
        self.reads += 1
        if self.reads > self._after:
            self.cancel(f"interrupted after {self._after} entries")
            return True
        return False


@pytest.mark.parametrize("after_entries", [0, 1, 2, 3])
def test_interruption_at_four_points_in_a_content_pass_publishes_nothing(tmp_path, after_entries):
    """The deepening pass's own interruption test, matching the census's five points.

    Content work is where interruption matters most: it is the only pass that
    opens files, and without the check it would be the only one that publishes a partial
    run when interrupted.
    """
    from ingence.storage.commit import read_pointer

    pool = pool_with_containers(tmp_path)
    # More candidates than the largest interruption point, or the loop finishes
    # before the token fires and the test asserts nothing.
    for index in range(4):
        (pool / f"reading_{index}.csv").write_text("sample,value\n1,2\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    baseline = run_index(project, "1")
    token = _CancelsAfter(after_entries)
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=120.0,
        token=token,
    )

    assert report.cancelled
    assert live_child_count() == 0
    pointer = read_pointer(project.path)
    assert pointer is not None
    assert pointer.run_id == baseline.run_id


def test_the_permitted_adapter_list_is_the_only_one_that_runs(tmp_path):
    """A setting that names what may run must be what runs.

    ``deepen_entry`` takes an ``extractors`` argument and honours it.
    ``ExtractionSettings.adapters`` is documented as "names of registered extractors permitted to
    run". The call site must join them. Otherwise a user narrowing the list would get every
    registered extractor anyway, and the run record would state a configuration the run did not
    have.

    This is the feature-flag mechanism: optional components are enabled by configuration and
    every run record says which were active. Without it, this file's own failure test would
    run two extractors nobody asked for, one of which exists to hang.

    Asserted by the observable consequence rather than by the call: a registry
    holding a hanging extractor and a crashing one, with only the crashing one
    permitted, records a failure for the crashing one and for no other.

    The test reads what was recorded, never how long the pass took. A time limit of five seconds
    would fail on a loaded machine with nothing wrong: the crash is detected at once, but starting
    the child process can take seconds when other work holds the cores. An extractor that ran
    although it was not permitted leaves its own record (the hanging one a timeout, after the
    ten-second ceiling), so the records say it without a clock.
    """
    from fixtures.hostile import HOSTILE_SUFFIX, register_all
    from ingence.extractors.base import EXTRACTORS

    names = register_all()
    try:
        pool = tmp_path / "pool"
        pool.mkdir()
        (pool / f"subject{HOSTILE_SUFFIX}").write_text("x")
        project = Project.create(
            tmp_path / "project",
            created_at="t",
            roots=[pool],
            settings=Settings(
                extraction=ExtractionSettings(
                    enabled=True,
                    adapters=("hostile_crash",),
                    subprocess_timeout_seconds=10.0,
                )
            ),
        )
        run_index(project, "1")
        run_deepen(
            project,
            run_id="run_20260101T000002Z_222222222222",
            started_at="t",
            budget_seconds=120.0,
        )
        with open_catalog(project.path) as catalog:
            recorded = catalog.query("SELECT operation, error_type FROM scan_errors").fetchall()
    finally:
        for name in names:
            EXTRACTORS.unregister(name)

    # Only the crashing extractor was permitted, so it is the only one with a record. The
    # hanging one, had it run, would have left a timeout under its own name.
    assert {row[0] for row in recorded} == {"extract.hostile_crash"}, (
        f"the records name {sorted({row[0] for row in recorded})}; "
        f"an extractor outside the permitted list ran"
    )
    assert "IsolationTimeoutError" not in {row[1] for row in recorded}


def test_an_extractor_failure_is_recorded_as_a_coverage_problem(tmp_path):
    """A parser that could not read a file it opened is comprehension, not access.

    **The distinction routes the fix and that is the whole point.** Access means
    fix the mount. Comprehension means write an extractor. A report that
    collapsed them would send somebody to the permissions when the answer was a
    missing format, and — worse — a rule tuned against comprehension failures is
    a correct rule being loosened to compensate for data it never saw.
    """
    from fixtures.hostile import HOSTILE_SUFFIX, register_all
    from ingence.core.schema import FailureClass
    from ingence.extractors.base import EXTRACTORS

    names = register_all()
    try:
        pool = tmp_path / "pool"
        pool.mkdir()
        target = pool / f"subject{HOSTILE_SUFFIX}"
        target.write_text("x")
        project = Project.create(
            tmp_path / "project",
            created_at="t",
            roots=[pool],
            settings=Settings(
                extraction=ExtractionSettings(
                    enabled=True,
                    adapters=("hostile_crash",),
                    subprocess_timeout_seconds=20.0,
                )
            ),
        )
        run_index(project, "1")
        run_deepen(
            project,
            run_id="run_20260101T000002Z_222222222222",
            started_at="t",
            budget_seconds=120.0,
        )
        with open_catalog(project.path) as catalog:
            rows = catalog.query("SELECT operation, failure_class FROM scan_errors").fetchall()
    finally:
        for name in names:
            EXTRACTORS.unregister(name)

    assert rows, "the crashing extractor should have produced an error"
    assert {row[1] for row in rows} == {FailureClass.COMPREHENSION.value}
    assert all(row[0].startswith("extract.") for row in rows)


# ---------------------------------------------------------------------------
# What a content pass writes, and what it carries forward.
# ---------------------------------------------------------------------------


def _references_rows(project) -> list[tuple[str, ...]]:
    with open_catalog(project.path) as catalog:
        return catalog.query(
            "SELECT e.entry_kind, o.observer, o.value_kind, o.extraction_tier, o.value_structure "
            "FROM observations o JOIN entries e USING (entry_id) "
            "WHERE o.key = 'references.paths' ORDER BY o.entry_id"
        ).fetchall()


def _extracting(tmp_path, pool, **extraction):
    return Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True, **extraction)),
    )


def test_the_paths_an_extractor_finds_mentioned_are_recorded(tmp_path):
    """References were collected and never written, so no rule could read one.

    Asserted in the published run rather than on the extractor's result, because
    the extractor has always returned them. The defect was that nothing between
    the result and the table kept them.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "protocol.txt").write_text(
        'Read "data/raw/acquisition.csv" and write results/summary.csv for the run\n'
    )
    project = _extracting(tmp_path, pool)
    run_index(project, "1")
    run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
    )

    rows = _references_rows(project)
    assert len(rows) == 1
    kind, observer, value_kind, tier, value = rows[0]
    assert (kind, observer, value_kind, tier) == ("filesystem", "text", "structure", "heuristic")
    assert json.loads(value) == ["data/raw/acquisition.csv", "results/summary.csv"]


def test_a_container_member_s_references_are_recorded_against_the_member(tmp_path):
    """A protocol inside an archive mentions paths as much as one beside it does."""
    import zipfile

    pool = tmp_path / "pool"
    pool.mkdir()
    with zipfile.ZipFile(pool / "bundle.zip", "w") as archive:
        archive.writestr(
            "notes/protocol.txt", "Inputs are in data/raw/acquisition.csv for this run\n"
        )
    project = _extracting(tmp_path, pool)
    run_index(project, "1")
    run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
    )

    rows = _references_rows(project)
    assert [(row[0], row[1]) for row in rows] == [("derived", "text")]
    assert json.loads(rows[0][4]) == ["data/raw/acquisition.csv"]


def test_a_member_that_hangs_is_stopped_at_its_container_s_clock_and_the_pass_finishes(tmp_path):
    """A parser that declares a boundary has one for a file inside an archive too.

    With this fixture and a two-second ceiling, a hanging parser must be killed and recorded
    as a file on disk and as a member of a ZIP alike. The pass runs on a daemon thread with a
    ceiling of its own here, so a regression fails this test rather than hanging the suite.

    Under the read pool the container's own clock is the boundary. The member's reader hangs inside
    the worker, the worker is killed at ``file_seconds`` in the stage it reported (the cascade), and
    the container is the file recorded as unread.
    """
    import zipfile

    from fixtures.hostile import HOSTILE_SUFFIX, register_all
    from ingence.extractors.base import EXTRACTORS

    names = register_all()
    try:
        pool = tmp_path / "pool"
        pool.mkdir()
        with zipfile.ZipFile(pool / "bundle.zip", "w") as archive:
            archive.writestr(f"inside{HOSTILE_SUFFIX}", "x")
        project = _extracting(
            tmp_path,
            pool,
            adapters=("archive", "hostile_hang"),
            subprocess_timeout_seconds=2.0,
            file_seconds=2.0,
        )
        run_index(project, "1")
        raised: list[BaseException] = []
        finished = threading.Event()

        def deepen() -> None:
            try:
                run_deepen(
                    project,
                    run_id="run_20260101T000002Z_222222222222",
                    started_at="t",
                    budget_seconds=120.0,
                )
            except BaseException as exc:  # re-raised on the test's own thread below
                raised.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=deepen, daemon=True)
        started = time.monotonic()
        worker.start()
        # Only bounds a pass that never ends (the hanging reader sleeps for an hour): a pass that
        # keeps its boundary ends at once, however slow the machine is to start the child.
        finished.wait(timeout=600.0)
        elapsed = time.monotonic() - started
        assert finished.is_set(), (
            f"the pass had not finished after {elapsed:.0f}s against a 2s ceiling; a "
            f"member's extractor ran without the process boundary it declared"
        )
        if raised:
            raise raised[0]
        with open_catalog(project.path) as catalog:
            rows = catalog.query(
                "SELECT s.operation, s.error_type, e.entry_kind "
                "FROM scan_errors s JOIN entries e USING (entry_id)"
            ).fetchall()
    finally:
        for name in names:
            EXTRACTORS.unregister(name)

    assert ("cascade", "WorkerTimeoutError", "filesystem") in rows
    assert live_child_count() == 0


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod cannot make a directory untraversable on Windows, so the census has "
    "nothing to fail on; the same fixture limit test_census records",
)
def test_a_census_failure_survives_a_content_pass(tmp_path):
    """A content pass wrote only its own errors, so the census's disappeared.

    Observations and edges were read back from the previous run and written again;
    scan errors were not. A run published by a dive therefore recorded no access
    failure at all, and a rule looking for concentrated access failures would have
    had nothing to read in exactly the runs that had read the most.
    """
    from ingence.core.schema import FailureClass

    project, pool = project_with_pool(tmp_path)
    locked = pool / "locked"
    locked.mkdir()
    (locked / "inside.txt").write_text("x")
    locked.chmod(0o000)
    try:
        run_index(project, "1")
        query = "SELECT relative_path, operation, failure_class FROM scan_errors"
        with open_catalog(project.path) as catalog:
            before = set(catalog.query(query).fetchall())
        run_deepen(
            project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
        )
        with open_catalog(project.path) as catalog:
            after = set(catalog.query(query).fetchall())
    finally:
        locked.chmod(0o755)

    assert before, "the unreadable directory should have produced a census error"
    assert {row[2] for row in before} == {FailureClass.ACCESS.value}
    assert before <= after


def test_what_a_pass_re_reads_replaces_what_the_last_pass_recorded(tmp_path):
    """An entry re-read after a failure is recorded once.

    If carried observations were written back before the pass ran, and the pass then recorded
    the same readings again under the same identifiers, a table declaring ``observation_id``
    its primary key would gain duplicate rows every time a failed file was read again. Nothing
    enforces a primary key before a commit, so nothing else would notice. What a pass re-reads
    replaces what the last one recorded about it. Everything it did not re-read is carried
    unchanged.
    """
    from fixtures.hostile import HOSTILE_SUFFIX, register_all
    from ingence.extractors.base import EXTRACTORS

    names = register_all()
    try:
        pool = tmp_path / "pool"
        pool.mkdir()
        (pool / f"subject{HOSTILE_SUFFIX}").write_text("x")
        project = _extracting(
            tmp_path, pool, adapters=("hostile_crash",), subprocess_timeout_seconds=10.0
        )
        run_index(project, "1")
        for suffix in ("2", "3", "4"):
            run_deepen(
                project,
                run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}",
                started_at="t",
                budget_seconds=60.0,
            )
        with open_catalog(project.path) as catalog:
            rows, distinct = catalog.query(
                "SELECT count(*), count(DISTINCT observation_id) FROM observations"
            ).fetchone()
            errors = catalog.query("SELECT operation FROM scan_errors").fetchall()
    finally:
        for name in names:
            EXTRACTORS.unregister(name)

    assert rows == distinct
    assert errors == [("extract.hostile_crash",)]


def test_a_content_pass_checks_the_claims_it_carries_forward(tmp_path, monkeypatch):
    """The claims are carried into the new run, so they are published with it.

    The pass must hand the invariant checks the claims, not an empty tuple. With an empty tuple,
    the INV-8 check, which reads closed-world claims, would pass without reading any.
    """
    import ingence.scan.deepen as deepen_module

    seen = []
    original = deepen_module.check_all

    def recording(snapshot):
        seen.append(snapshot)
        return original(snapshot)

    monkeypatch.setattr(deepen_module, "check_all", recording)
    project, _ = project_with_pool(tmp_path)
    run_index(project, "1")
    with open_catalog(project.path) as catalog:
        census_claims = catalog.count("claims")
    run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
    )

    assert census_claims > 0
    assert len(seen) == 1
    assert len(seen[0].claims) == census_claims


# ---------------------------------------------------------------------------
# What the pass writes to its journal.
# ---------------------------------------------------------------------------


def _journal_rows(project, event: str) -> list[tuple[str | None, int | None, dict]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT entry_id, duration_ns, detail FROM journal_events WHERE event = ? "
            "ORDER BY sequence",
            [event],
        ).fetchall()
    return [(entry_id, duration, json.loads(detail)) for entry_id, duration, detail in rows]


def _dived_with_journal(tmp_path, **journal):
    from ingence.core.settings import JournalSettings

    pool = build_pool(tmp_path)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(journal=JournalSettings(**journal)),
    )
    run_index(project, "1")
    report = run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
    )
    return project, report


def test_each_examined_entry_says_where_its_time_went_stage_by_stage(tmp_path):
    """One duration per entry could not say whether a slow file was slow to read or to parse."""
    project, report = _dived_with_journal(tmp_path)
    rows = _journal_rows(project, "entry.examined")
    assert len(rows) == report.examined
    for _, duration, detail in rows:
        stages = {stage: (nanoseconds, read) for stage, nanoseconds, read, _ in detail["stages"]}
        assert "detect" in stages
        assert any(stage.startswith("identity.") for stage in stages)
        assert all(nanoseconds >= 0 for nanoseconds, _ in stages.values())
        # The stages are parts of the entry's own time, never more than it.
        assert sum(nanoseconds for nanoseconds, _ in stages.values()) <= duration
    csv_stages = {
        stage
        for _, _, detail in rows
        for stage, _, _, _ in detail["stages"]
        if detail.get("size_bytes") is not None
    }
    assert "extract.tabular" in csv_stages


def test_the_pass_totals_its_stages_and_times_its_publishing(tmp_path):
    project, report = _dived_with_journal(tmp_path)
    (summary,) = _journal_rows(project, "run.examined")
    detail = summary[2]
    assert detail["examined"] == report.examined
    totals = {stage: (count, nanoseconds) for stage, count, nanoseconds, _ in detail["stages"]}
    assert totals["detect"][0] == report.examined
    # The journal stays open through the invariant checks and the compaction, and the three phases
    # it cannot see are on the report.
    assert _journal_rows(project, "publish.invariants.finished")
    assert _journal_rows(project, "publish.compaction.finished")
    assert list(report.publish_seconds) == [
        "invariants",
        "compaction",
        "journal",
        "quality",
        "manifest",
        "commit",
    ]


def test_per_entry_journaling_off_writes_no_entry_rows_and_keeps_the_totals(tmp_path):
    """Per-entry journaling off writes no entry rows and keeps the totals."""
    project, report = _dived_with_journal(tmp_path, per_entry=False)
    assert report.examined > 0
    assert _journal_rows(project, "entry.examined") == []
    (summary,) = _journal_rows(project, "run.examined")
    assert summary[2]["stages"]


def test_timings_off_records_no_stage_and_no_publishing_phase(tmp_path):
    project, report = _dived_with_journal(tmp_path, timings=False)
    rows = _journal_rows(project, "entry.examined")
    assert rows
    assert all("stages" not in detail for _, _, detail in rows)
    (summary,) = _journal_rows(project, "run.examined")
    assert "stages" not in summary[2]
    assert _journal_rows(project, "publish.invariants.finished") == []
    # Publishing is still timed for the terminal, which reads the report.
    assert report.publish_seconds


# ---------------------------------------------------------------------------
# A wall clock for in-process extraction above a size.
# ---------------------------------------------------------------------------


def _stage_isolation(project, stage: str) -> dict[str, bool]:
    """Return, per examined file name, whether the named stage ran behind the boundary."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT relative_path, detail FROM journal_events WHERE event = 'entry.examined'"
        ).fetchall()
    found: dict[str, bool] = {}
    for relative_path, detail in rows:
        for name, _, _, isolated in json.loads(detail)["stages"]:
            if name == stage:
                found[relative_path] = isolated
    return found


def test_an_in_process_extractor_on_large_content_is_stopped_by_the_wall_clock(tmp_path):
    """A large, slow text file read in process is stopped by the wall clock.

    The read worker is the boundary. A reader that declares it runs in process runs in the worker,
    and the file's own clock (``file_seconds``) is what stops it, the parent killing the worker
    at the stage it reported and starting another.
    """
    from fixtures.hostile import HOSTILE_SUFFIX, register_in_process
    from ingence.extractors.base import EXTRACTORS

    names = register_in_process()
    try:
        pool = tmp_path / "pool"
        pool.mkdir()
        (pool / f"large{HOSTILE_SUFFIX}").write_bytes(b"x" * 4096)
        project = _extracting(
            tmp_path,
            pool,
            adapters=names,
            subprocess_timeout_seconds=2.0,
            file_seconds=2.0,
            supervise_in_process_above_bytes=1024,
        )
        run_index(project, "1")
        report = run_deepen(
            project,
            run_id="run_20260101T000002Z_222222222222",
            started_at="t",
            budget_seconds=120.0,
        )
    finally:
        for name in names:
            EXTRACTORS.unregister(name)

    # Bounded, said by the records and not by a clock (a busy machine takes seconds to start the
    # worker): the reading was ended at the file's ceiling and recorded as timed out under the
    # stage it was in. Had it run its minute, it would have finished and recorded no failure.
    assert report.failed == 1
    with open_catalog(project.path) as catalog:
        errors = catalog.query("SELECT operation, error_type FROM scan_errors").fetchall()
    assert errors == [("extract.hostile_slow_in_process", "WorkerTimeoutError")]
    assert _stage_isolation(project, "extract.hostile_slow_in_process") == {
        f"large{HOSTILE_SUFFIX}": True
    }
    assert live_child_count() == 0


def test_small_content_stays_in_process_and_large_content_reads_the_same_behind_the_boundary(
    tmp_path,
):
    """The boundary moves where a reading runs, never what it reads.

    Nothing is started per file. The read worker is the boundary for small and large alike, so
    no stage reports a child of its own, and the two files' readings are the same.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    line = "a line of prose naming data/raw/plate_01.csv once\n"
    (pool / "small.txt").write_text(line * 2)
    (pool / "large.txt").write_text(line * 100)
    project = _extracting(tmp_path, pool, adapters=("text",), supervise_in_process_above_bytes=1024)
    run_index(project, "1")
    run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
    )

    assert _stage_isolation(project, "extract.text") == {"small.txt": False, "large.txt": False}
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, o.key FROM observations o JOIN entries e USING (entry_id) "
            "WHERE o.observer = 'text' ORDER BY 1, 2"
        ).fetchall()
    keys = {name: sorted(key for row_name, key in rows if row_name == name) for name, _ in rows}
    assert keys["small.txt"] == keys["large.txt"]
    assert "references.paths" in keys["large.txt"]


# ---------------------------------------------------------------------------
# The hash settings choose the byte digest.
# ---------------------------------------------------------------------------


def _digests_run(tmp_path, *, size: int = 4096, **hashing) -> set[str]:
    path = tmp_path / "sample.bin"
    path.write_bytes(b"\x01" * size)
    outcome = deepen_entry(
        entry_for(path, "sample.bin"),
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(hash=HashSettings(**hashing)),
    )
    return {
        observation.key.removeprefix("identity.")
        for observation in outcome.observations
        if observation.key in {"identity.blake3_whole", "identity.blake3_quick"}
    }


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (HashMode.NONE, set()),
        (HashMode.QUICK, {"blake3_quick"}),
        (HashMode.FULL, {"blake3_whole"}),
        (HashMode.ADAPTIVE, {"blake3_whole"}),
    ],
)
def test_the_hash_mode_decides_which_byte_digest_a_file_gets(tmp_path, mode, expected):
    """The hash mode decides which byte digest a file gets."""
    assert _digests_run(tmp_path, mode=mode) == expected


def test_adaptive_hashing_samples_above_its_threshold(tmp_path):
    assert _digests_run(tmp_path, mode=HashMode.ADAPTIVE, full_hash_max_bytes=1024) == {
        "blake3_quick"
    }


def test_the_hash_filters_decide_which_files_are_digested_at_all(tmp_path):
    assert _digests_run(tmp_path, mode=HashMode.FULL, max_file_bytes=1024) == set()
    assert _digests_run(tmp_path, mode=HashMode.FULL, exclude_extensions=("bin",)) == set()
    assert _digests_run(tmp_path, mode=HashMode.FULL, include_extensions=("csv",)) == set()
    assert _digests_run(tmp_path, mode=HashMode.FULL, include_extensions=("bin",)) == {
        "blake3_whole"
    }


def test_a_standard_dive_samples_rather_than_reading_every_byte(tmp_path):
    """A dive under a profile that says quick does not read every byte it examines."""
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "large.dat").write_bytes(b"\x07" * (3 << 20))
    # The depth's preset chooses the digest here (``need_from="depth"``).
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(hash=HashSettings(need_from="depth")),
    )
    run_index(project, "1")
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        profile="standard",
        budget_seconds=60.0,
    )
    with open_catalog(project.path) as catalog:
        methods = {
            row[0]
            for row in catalog.query(
                "SELECT observer FROM observations WHERE identity_method IS NOT NULL"
            ).fetchall()
        }
    assert "blake3_quick" in methods
    assert "blake3_whole" not in methods
    # The digest's own reading is a sample of each end, whatever the extractors read.
    (summary,) = _journal_rows(project, "run.examined")
    stages = {stage: read for stage, _, _, read in summary[2]["stages"]}
    assert "identity.blake3_whole" not in stages
    assert stages["identity.blake3_quick"] <= 2 * 64 * 1024
    assert report.examined == 1


# ---------------------------------------------------------------------------
# What libraries say goes to the journal, not the terminal.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("isolated", [True, False])
def test_what_a_library_says_about_a_file_is_journalled_and_never_printed(
    tmp_path, capfd, isolated
):
    """A dive does not print a flood of library warnings into a person's terminal."""
    from fixtures.hostile import HOSTILE_SUFFIX, register_all
    from ingence.extractors.base import EXTRACTORS

    names = register_all()
    try:
        pool = tmp_path / "pool"
        pool.mkdir()
        (pool / f"talkative{HOSTILE_SUFFIX}").write_text("x")
        project = _extracting(
            tmp_path,
            pool,
            adapters=("hostile_noisy",),
            use_subprocess_isolation=isolated,
        )
        run_index(project, "1")
        capfd.readouterr()
        run_deepen(
            project,
            run_id="run_20260101T000002Z_222222222222",
            started_at="t",
            budget_seconds=60.0,
        )
        printed = capfd.readouterr()
    finally:
        for name in names:
            EXTRACTORS.unregister(name)

    assert "wrong pointing object" not in printed.out + printed.err
    assert "palette" not in printed.out + printed.err
    (row,) = _journal_rows(project, "entry.examined")
    said = [message for stage, message in row[2]["warnings"]]
    assert all(stage == "extract.hostile_noisy" for stage, _ in row[2]["warnings"])
    assert "noisy.library: ignoring a wrong pointing object (x100)" in said
    assert "noisy.library: a font nobody installed" in said
    assert "UserWarning: palette images should be converted" in said
    (summary,) = _journal_rows(project, "run.examined")
    assert summary[2]["warnings"] == len(said)


def test_a_summary_keeps_a_few_distinct_messages_cut_short_and_counts_the_rest():
    from ingence.extractors.captured import MESSAGE_WIDTH, MESSAGES_KEPT, summarise

    messages = ["often"] * 50 + [f"once {index}" for index in range(10)] + ["x" * 1000]
    kept = summarise(messages)
    assert kept[0] == "often (x50)"
    assert len(kept) == MESSAGES_KEPT + 1
    assert kept[-1] == f"and {len(messages) - 50 - (MESSAGES_KEPT - 1)} more"
    assert all(len(line) <= MESSAGE_WIDTH + 10 for line in kept)
    assert summarise([]) == ()


# ---------------------------------------------------------------------------
# The dive as a control a person sets.
# ---------------------------------------------------------------------------


def test_a_depth_is_named_the_way_a_person_says_it() -> None:
    """``metadata`` is the word for the profile the engine spells ``census``."""
    from ingence.scan.profiles import DEPTHS, depth_named

    assert depth_named("metadata") is ProfileName.CENSUS
    assert depth_named(" DEEP ") is ProfileName.DEEP
    assert [depth.value for depth in DEPTHS] == ["census", "standard", "deep"]
    with pytest.raises(ValueError, match="not a depth"):
        depth_named("shallow")


def test_a_project_declares_its_own_depth_and_a_dive_honours_it(tmp_path) -> None:
    """How deep to go is the user's answer, kept where a later command cannot forget it."""
    from ingence.core.settings import Settings as ProjectSettings

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "a.txt").write_text("hello")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=ProjectSettings(depth="metadata"),
    )
    assert Project.open(project.path).settings.depth == "metadata"
    # The default is unchanged for every project that says nothing.
    assert ProjectSettings().depth == "standard"


def test_the_ceiling_for_one_reading_scales_with_the_file(tmp_path) -> None:
    """A base and a rate, so being large is not recorded as being broken (C2 item 3)."""
    from ingence.extractors.limits import Limits, ceiling_for

    settings = ExtractionSettings()
    assert ceiling_for(0, settings) == settings.subprocess_timeout_seconds
    assert ceiling_for(None, settings) == settings.subprocess_timeout_seconds
    # Three minutes as the order for a gigabyte, which is the ceiling a working reader
    # of a large instrument file needs and a stalled one still cannot survive.
    assert 150.0 <= ceiling_for(1 << 30, settings) <= 240.0
    assert ceiling_for(4 << 30, settings) > ceiling_for(1 << 30, settings)
    # The limits handed to a reading carry the same clock, rather than a second copy.
    assert Limits.from_settings(settings, size_bytes=1 << 30).timeout_seconds == ceiling_for(
        1 << 30, settings
    )
    flat = ExtractionSettings(subprocess_timeout_per_gigabyte_seconds=0.0)
    assert ceiling_for(1 << 30, flat) == flat.subprocess_timeout_seconds


def test_a_header_reader_is_asked_before_a_body_reader() -> None:
    """Ordered by what each extractor declared, so no list of names lives in the dive."""
    from ingence.extractors import load_builtins
    from ingence.extractors.base import EXTRACTORS, ReadingDepth, reading_depth_of
    from ingence.scan.deepen import _header_first

    load_builtins()
    names = EXTRACTORS.names(available_only=True)
    ordered = _header_first(names)
    depths = [reading_depth_of(EXTRACTORS.get(name)) is ReadingDepth.HEADER for name in ordered]
    assert depths == sorted(depths, reverse=True)
    assert set(ordered) == set(names)
    # Every vendor format the engine reads for an instrument clock declares a header.
    assert reading_depth_of(EXTRACTORS.get("nikon_nd2")) is ReadingDepth.HEADER
    assert reading_depth_of(EXTRACTORS.get("text")) is ReadingDepth.CONTENT


def test_an_extension_a_person_answered_never_about_is_still_opened(tmp_path) -> None:
    """``never`` orders the spend and vetoes nothing.

    Skipped by policy keeps one meaning: there is nothing here to read. So a person's answer
    and an empty file are never counted as one thing.
    """
    import inspect

    pool = tmp_path / "pool"
    pool.mkdir()
    target = pool / "sample.zorbex"
    target.write_text("something a person said was never worth reading")
    entry = entry_for(target, "sample.zorbex")
    settings = Settings(extraction=ExtractionSettings(enabled=True))

    read = deepen_entry(entry, target, run_id="r", observed_at="t", settings=settings)
    assert read.outcome is not ScanOutcome.SKIPPED_BY_POLICY
    assert read.observations
    # There is no answer left for one file's pass to take: the answer is the order's alone.
    assert "worth_reading" not in inspect.signature(deepen_entry).parameters


def test_within_a_round_the_unanswered_come_first_then_no_then_never() -> None:
    """Every family still takes its turn in the first round; the answer decides only when."""
    entries = _sized(
        [
            ("a.csv", 1),
            ("b.log", 1),
            ("c.nd2", 1),
            ("d.zorbex", 1),
            ("a2.csv", 2),
            ("b2.log", 2),
            ("d2.zorbex", 2),
        ]
    )
    typed = _typed(csv="table", log="log_file", nd2="vendor")
    plain = [entry.name for entry in spend_order(entries, file_type=typed)]
    answers = {"LOG": "no", "zorbex": "never"}
    said = [entry.name for entry in spend_order(entries, answers=answers, file_type=typed)]
    assert said[:4] == ["a.csv", "c.nd2", "b.log", "d.zorbex"]
    assert said[4:] == ["a2.csv", "b2.log", "d2.zorbex"]
    assert sorted(said) == sorted(plain)
    # "yes" is the same as not answering.
    assert spend_order(entries, answers={"csv": "yes"}, file_type=typed) == spend_order(
        entries, file_type=typed
    )
    # Within one family the answer decides which file takes the family's turn, so with no
    # vocabulary at all -- every extension in the one unbound family -- the answered go last.
    alone = [entry.name for entry in spend_order(entries, answers=answers)]
    assert alone == ["a.csv", "c.nd2", "a2.csv", "b.log", "b2.log", "d.zorbex", "d2.zorbex"]


def _typed(**families: str):
    """A file-type lookup standing in for the Dictionary, by extension."""
    return lambda entry: families.get((entry.extension or "").casefold())


def test_a_family_is_the_file_type_so_two_spellings_of_one_format_share_a_turn() -> None:
    """``.tif`` and ``.tiff`` are one kind of file, and take one turn between them."""
    entries = _sized(
        [("a.tif", 1), ("b.tiff", 2), ("c.tif", 3), ("d.csv", 5), ("e.csv", 6), ("f.nd2", 9)]
    )
    by_extension = [entry.name for entry in spend_order(entries, SpendOrder.EXTENSION_ROUND_ROBIN)]
    # The extension rotation gives each spelling a turn of its own.
    assert by_extension[:4] == ["a.tif", "b.tiff", "d.csv", "f.nd2"]
    typed = _typed(tif="image", tiff="image", csv="table", nd2="vendor")
    by_type = [entry.name for entry in spend_order(entries, file_type=typed)]
    # Each type's smallest, then each type's second: the TIFF family's second file waits.
    assert by_type == ["a.tif", "d.csv", "f.nd2", "b.tiff", "e.csv", "c.tif"]


def test_a_type_s_turns_rotate_over_its_spellings_so_the_smallest_cannot_take_them_all() -> None:
    """A broad type's turns rotate over its spellings, so the smallest cannot take them all.

    If every turn of the vendor image type went to its smallest spelling, and every turn of the
    image type to its smallest, a whole dive would reach no file of the formats whose readers
    give instrument clocks.
    """
    small = [(f"s{index}.qa", 1 + index) for index in range(4)]
    large = [(f"l{index}.qb", 1_000 + index) for index in range(2)]
    entries = _sized([*small, *large, ("t.csv", 5)])
    typed = _typed(qa="vendor", qb="vendor", csv="table")
    ordered = [entry.name for entry in spend_order(entries, file_type=typed)]
    vendor = [name for name in ordered if not name.endswith(".csv")]
    assert vendor[:4] == ["s0.qa", "l0.qb", "s1.qa", "l1.qb"]


def test_the_unbound_share_one_slot_and_take_its_turns_over_their_own_extensions() -> None:
    """Many one-off suffixes cannot swamp the rotation, and each is still reached.

    Without the vocabulary every extension is unbound, and the order is the extension
    rotation exactly: the unit changes only where the Dictionary names a type.
    """
    junk = [(f"x{index}.q{index}", 1) for index in range(6)]
    entries = _sized([("a.csv", 2), ("b.csv", 3), ("c.csv", 4), *junk])
    typed = _typed(csv="table")
    ordered = [entry.name for entry in spend_order(entries, file_type=typed)]
    # One junk file a round, beside one table a round, until the tables run out.
    assert ordered[:6] == ["x0.q0", "a.csv", "x1.q1", "b.csv", "x2.q2", "c.csv"]
    # Every one-off suffix is reached in the end.
    assert sorted(ordered) == sorted(entry.name for entry in entries)
    # With no vocabulary at all the unbound family is the whole pool and nothing moves.
    assert spend_order(entries) == spend_order(entries, SpendOrder.EXTENSION_ROUND_ROBIN)


def test_within_a_family_a_person_s_answer_decides_which_file_takes_the_turn() -> None:
    """A type's turn goes to the files nobody objected to before those somebody did."""
    entries = _sized([("a.svg", 1), ("b.png", 5), ("c.png", 6), ("d.csv", 3)])
    typed = _typed(svg="image", png="image", csv="table")
    ordered = spend_order(entries, answers={"svg": "never"}, file_type=typed)
    assert [entry.name for entry in ordered] == ["d.csv", "b.png", "c.png", "a.svg"]


def test_within_a_family_an_instrument_s_spelling_leads_its_turns() -> None:
    """One image type, and the instrument's spellings read first.

    Images are one type, so the formats instruments write share one family's turns with every
    screenshot and figure. Without a lead for the instrument's spellings, the ND2 files would be
    far fewer among the first files a dive reads. The spellings an instrument writes rotate among
    themselves ahead of the family's others, within each answer tier, and the family still takes
    one turn a round.
    """
    entries = _sized(
        [("a.qa", 1), ("b.qg", 2), ("c.qb", 50), ("d.qg", 3), ("e.qa", 60), ("t.csv", 4)]
    )
    typed = _typed(qa="image", qb="image", qg="image", csv="table")
    instrument = {"qa", "qb"}

    def leads(entry):
        return (entry.extension or "").casefold() in instrument

    plain = [entry.name for entry in spend_order(entries, file_type=typed)]
    led = [entry.name for entry in spend_order(entries, file_type=typed, leads=leads)]
    assert plain[:2] == ["a.qa", "t.csv"] and plain[2] == "b.qg"
    images = [name for name in led if not name.endswith(".csv")]
    assert images == ["a.qa", "c.qb", "e.qa", "b.qg", "d.qg"]
    # One turn a round for the family either way: the table's file is second.
    assert led[1] == "t.csv"
    # An answer still outranks an origin: a spelling somebody said never to read goes last.
    answered = spend_order(entries, answers={"qa": "never"}, file_type=typed, leads=leads)
    assert [entry.name for entry in answered if entry.name.endswith(".qa")] == ["a.qa", "e.qa"]
    assert [entry.name for entry in answered][-2:] == ["a.qa", "e.qa"]


def test_what_a_person_said_about_opening_each_extension_is_read_back(tmp_path) -> None:
    """The dive reads the Record, in the person's own words rather than the log's."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.record.targets import extension_target
    from ingence.record.verdicts import worth_reading

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "a.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260922T090000Z_census", started_at="2026-09-22T09:00:00Z")
    assert worth_reading(project.path) == {}
    for extension, kind in (
        ("ZORBEX", RecordEventKind.ANNOTATE),
        ("qux", RecordEventKind.REJECT),
        ("baz", RecordEventKind.CONFIRM),
    ):
        append_event(
            project.path,
            kind=kind,
            target=extension_target(extension),
            actor="robin",
            occurred_at="2026-09-22T09:00:00Z",
            against_run_id="run_20260922T090000Z_census",
        )
    assert worth_reading(project.path) == {"zorbex": "never", "qux": "no", "baz": "yes"}
    # A person who changes their mind changes the run, and the log keeps both.
    append_event(
        project.path,
        kind=RecordEventKind.CONFIRM,
        target=extension_target("zorbex"),
        actor="robin",
        occurred_at="2026-09-22T10:00:00Z",
        against_run_id="run_20260922T090000Z_census",
    )
    assert worth_reading(project.path)["zorbex"] == "yes"


def test_a_large_file_whose_header_answered_keeps_its_body_unread(tmp_path) -> None:
    """The saving item 2 exists for, and the case it is careful about (C2 item 2).

    ``max_sample_bytes`` is the threshold *and* is set small here, which is the point of a
    parameter: a 2 KB file stands for a gigabyte and the test needs no gigabyte. Above it a
    content reading is already partial, so a header that answered leaves nothing worth the
    body; a header that answered nothing leaves the body exactly as it was.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    # A real TIFF header, which the detectors name and the TIFF reader reads: little-endian
    # magic, one IFD with no entries. Built from the format's structure, not from anybody's
    # file.
    tiff = pool / "plate.tif"
    tiff.write_bytes(b"II\x2a\x00\x08\x00\x00\x00" + b"\x00\x00" + b"\x00" * 2040)
    opaque = pool / "plate.zorbex"
    opaque.write_bytes(b"\x9d\x17\x4b" * 700)

    settings = Settings(
        extraction=ExtractionSettings(enabled=True, max_sample_bytes=1024, metadata_first=True)
    )
    read_tiff = deepen_entry(
        entry_for(tiff, "plate.tif"),
        tiff,
        run_id="r",
        observed_at="t",
        settings=settings,
    )
    read_opaque = deepen_entry(
        entry_for(opaque, "plate.zorbex"),
        opaque,
        run_id="r",
        observed_at="t",
        settings=settings,
    )

    def said(outcome) -> list[str]:
        return [message for key, message in outcome.warnings if key == "metadata_first"]

    assert len(said(read_tiff)) == 1
    assert "body was not read" in said(read_tiff)[0]
    # The header reading still happened: the file was examined and its type recorded.
    assert read_tiff.outcome is not ScanOutcome.SKIPPED_BY_POLICY
    assert any(observation.key.startswith("type.") for observation in read_tiff.observations)
    # A file nobody could identify is the one worth opening properly, and it is opened.
    assert said(read_opaque) == []

    # And the whole policy is one setting away from being off.
    without = deepen_entry(
        entry_for(tiff, "plate.tif"),
        tiff,
        run_id="r",
        observed_at="t",
        settings=Settings(
            extraction=ExtractionSettings(enabled=True, max_sample_bytes=1024, metadata_first=False)
        ),
    )
    assert said(without) == []


def test_a_dive_and_an_inference_carry_what_the_census_left_out(tmp_path):
    """A run is a complete snapshot, and what the walk left out is the census's to say.

    Each later pass carries it. Otherwise a view reading the current run shows nothing left out
    once a dive has run.
    """
    from ingence.infer.run import run_inference

    project, pool = project_with_pool(tmp_path)
    (pool / ".hidden_notes.txt").write_text("left out as a hidden name")
    run_index(project, "1")
    with open_catalog(project.path) as catalog:
        (census,) = catalog.query("SELECT excluded_json FROM run").fetchone()
    assert "hidden" in census
    run_deepen(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        assert catalog.query("SELECT excluded_json FROM run").fetchone() == (census,)
    run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    with open_catalog(project.path) as catalog:
        assert catalog.query("SELECT excluded_json FROM run").fetchone() == (census,)


def test_a_binary_file_whose_header_answered_keeps_its_body_unread_at_any_size(tmp_path) -> None:
    """Header first at any size.

    The header-first policy applies at any size, not only above ``max_sample_bytes``. Otherwise a
    small binary file whose format reader had answered would still have its body harvested for
    strings. A file whose first bytes name a binary format, and whose header reader answered, has
    nothing in its body for a body reader, so the body readers are skipped whatever the size. A
    text file and a file nothing named are read whole: the sample ceiling here is far above every
    file, so nothing is "large".
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    # The same invented TIFF the large-file test uses, built from the format's structure.
    tiff = pool / "plate.tif"
    tiff.write_bytes(b"II\x2a\x00\x08\x00\x00\x00" + b"\x00\x00" + b"\x00" * 2040)
    notes = pool / "notes.txt"
    notes.write_text("some invented notes about a plate\n" * 40)
    opaque = pool / "plate.zorbex"
    opaque.write_bytes(b"\x9d\x17\x4b" * 700)

    settings = Settings(
        extraction=ExtractionSettings(enabled=True, max_sample_bytes=1 << 20, metadata_first=True)
    )

    def dived(path):
        return deepen_entry(
            entry_for(path, path.name), path, run_id="r", observed_at="t", settings=settings
        )

    def said(outcome) -> list[str]:
        return [message for key, message in outcome.warnings if key == "metadata_first"]

    read_tiff = dived(tiff)
    assert len(said(read_tiff)) == 1
    assert "body was not read" in said(read_tiff)[0]
    assert "binary format" in said(read_tiff)[0]
    assert read_tiff.outcome is ScanOutcome.EXTRACTED
    assert any(observation.key.startswith("type.") for observation in read_tiff.observations)
    # The body readers did not run: no strings were harvested from the file.
    assert not any(observation.key.startswith("strings.") for observation in read_tiff.observations)

    read_notes = dived(notes)
    assert said(read_notes) == []
    assert any(observation.key.startswith("strings.") for observation in read_notes.observations)

    read_opaque = dived(opaque)
    assert said(read_opaque) == []
    assert any(observation.key.startswith("strings.") for observation in read_opaque.observations)

    # And the whole policy is one setting away from being off, below the threshold too.
    without = deepen_entry(
        entry_for(tiff, "plate.tif"),
        tiff,
        run_id="r",
        observed_at="t",
        settings=Settings(
            extraction=ExtractionSettings(
                enabled=True, max_sample_bytes=1 << 20, metadata_first=False
            )
        ),
    )
    assert said(without) == []
    assert any(observation.key.startswith("strings.") for observation in without.observations)


_EIGHT_WORKERS_RUN = "run_20260101T000008Z_888888888888"


def test_the_same_pool_read_with_one_worker_and_with_eight_publishes_the_same_tables(tmp_path):
    """The determinism test for the read pool.

    The parent hands out work in spend order and is the only writer, so the published tables are
    the same for any number of workers, whatever order the workers finish in. Two projects over
    one pool, one dived with a single worker and one with eight, compared table by table.

    The timing fields left out are ``compare_runs``'s own: the ``run`` and ``journal_events``
    tables whole (a run record describes its run, and the journal records timings and
    scheduling), and in every other table the columns ``run_id``, ``first_seen_run``,
    ``last_seen_run``, ``against_run_id``, ``parent_run_id``, ``observed_at``, ``occurred_at``,
    ``started_at``, ``ended_at``, ``elapsed_ns``, ``duration_ns``, ``peak_bytes``, ``phase``,
    ``inode``, ``device``, ``uid`` and ``gid`` (``VOLATILE_COLUMNS``), and the schema stamp.
    """
    import shutil

    from ingence.storage.layout import RUNS_DIR

    pool = pool_with_containers(tmp_path)
    for index in range(6):
        (pool / f"reading_{index}.csv").write_text(f"sample,value\n{index},{index * 2}\n")
        (pool / f"notes_{index}.txt").write_text(f"Plate {index} read at 620 nm.\n" * (index + 1))
    projects = {}
    for workers in (1, 8):
        project = Project.create(
            tmp_path / f"project_{workers}",
            created_at="t",
            roots=[pool],
            settings=Settings(extraction=ExtractionSettings(enabled=True, workers=workers)),
        )
        run_index(project, "1")
        dive_id = "run_20260101T000002Z_222222222222" if workers == 1 else _EIGHT_WORKERS_RUN
        report = run_deepen(project, run_id=dive_id, started_at="t", budget_seconds=600.0)
        assert report.workers == workers
        assert report.examined > 8
        assert report.failed == 0
        projects[workers] = project
    assert live_child_count() == 0

    # Both dives' tables side by side in one project, so the determinism comparison reads
    # them: the comparison reads a run's directory and nothing of the project around it.
    both = projects[1].path
    shutil.copytree(
        projects[8].path / RUNS_DIR / _EIGHT_WORKERS_RUN, both / RUNS_DIR / _EIGHT_WORKERS_RUN
    )
    left, right = "run_20260101T000002Z_222222222222", _EIGHT_WORKERS_RUN
    assert compare_runs(both, left, right) == {}, describe_differences(both, left, right)
