"""Where a derivation is looked for, and which steps it may take.

Two rules, with invented names:

- A rung may be skipped where the identity holds no file at it. ``kelt_001.nd2`` beside
  ``kelt_001_results.csv``, nothing processed between them, is not refused for lack of a declared
  step from acquisition to analysis. A pair skipping a rung the identity does hold stays refused,
  so a chain is proposed link by link, and two files on one rung stay siblings.
- An identity is compared within an experiment. One experiment's chain is commonly split across
  its stage subfolders (``2020-02-21 zib kelt/{raw,processed,analysis}/kelt_001*``), and an
  identity compared within one folder would never draw such a pair.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import Claim, Defeater, Entry
from ingence.core.tiers import ResolutionStatus
from ingence.infer.subjects import EntryRecords, PairRecords
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES

if TYPE_CHECKING:
    from pathlib import Path

load_builtins()

_CHAIN = {"acquisition": "processed", "raw": "processed", "processed": "analysis"}
_SKIPPING = {
    "stage_transitions": _CHAIN,
    "refuse_siblings_on_one_rung": True,
    "maximum_rungs_skipped": 1,
}


def _entry(entry_id: str) -> Entry:
    return Entry(
        entry_id=entry_id,
        root_id="root",
        relative_path=f"kelt/{entry_id}",
        name=entry_id,
        depth=1,
        entry_type="file",
        extension="",
        compound_extension="",
        first_seen_run="r",
        last_seen_run="r",
    )


def _stage(subject: str, value: str) -> Claim:
    return Claim(
        claim_id=f"claim:{subject}:{value}",
        run_id="r",
        subject=subject,
        predicate="classifies.pipeline_stage_from_name",
        object_reference=value,
        rule_id="pipeline_stage_from_name",
        rule_version="1",
        scope=PROJECT_SCOPE,
        defeaters=(Defeater(reason="a test defeater", rule_id="defeater", rule_version="1"),),
        resolution_status=ResolutionStatus.RESOLVED,
    )


def _file(entry_id: str, *stages: str) -> EntryRecords:
    return EntryRecords(
        entry=_entry(entry_id), claims=tuple(_stage(entry_id, stage) for stage in stages)
    )


def _pair(source: EntryRecords, target: EntryRecords, *others: EntryRecords) -> PairRecords:
    """A pair drawn within one identity class, handed every member of the class."""
    return PairRecords(
        source=source,
        target=target,
        within="same_name_identity",
        source_membership=f"member:{source.entry.entry_id}",
        target_membership=f"member:{target.entry.entry_id}",
        members=(source, target, *others),
    )


def test_a_stage_step_proposes_nothing_whatever_rungs_its_class_holds() -> None:
    """A stage step proposes nothing, in every arrangement of rungs.

    The arrangements are a rung the identity holds no file at, a rung it does hold, two files on
    one rung, and a pair handed no class. A derivation is found by its own evidence and ordered
    by time (``tests/test_derivations_before_stages.py``)."""
    predicate = PREDICATES.get("derives_from_stage_transition")
    acquired = _file("kelt_001.nd2", "acquisition")
    processed = _file("kelt_001_processed.tif", "processed")
    results = _file("kelt_001_results.csv", "analysis")
    filtered = _file("kelt_001_results_filtered.csv", "analysis")
    summary = _file("kelt_001_summary.csv", "summary")
    chain = {**_CHAIN, "analysis": "summary"}
    for parameters in (
        _SKIPPING,
        {**_SKIPPING, "maximum_rungs_skipped": 2, "stage_transitions": chain},
    ):
        for pair in (
            _pair(acquired, results),
            _pair(acquired, processed, results),
            _pair(acquired, results, processed),
            _pair(results, filtered, acquired),
            _pair(acquired, summary),
            PairRecords(
                source=acquired,
                target=results,
                within="same_name_identity",
                source_membership="a",
                target_membership="b",
            ),
        ):
            assert predicate.evaluate(pair, parameters) is None
            assert "never creates" in predicate.explain_abstention(pair, parameters).reason


# ---------------------------------------------------------------------------
# End to end, under revision 4.
# ---------------------------------------------------------------------------


def _derived(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    """Index a pool of the given files, infer it under revision 4, and return its derivations,
    whichever rule proposed each."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    _write_in_turn(pool, files)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT s.relative_path, t.relative_path FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id "
            "WHERE c.primitive = 'derives'"
        ).fetchall()
    return {(_slashed(source), _slashed(target)) for source, target in rows}


def _write_in_turn(pool: Path, files: dict[str, str]) -> None:
    """Write each file an hour after the one listed before it.

    A pair that a name identity joins takes its direction from the earlier file, by its earliest
    credible time, and not from its stage words. So each pool lists its files in the order they
    were made."""
    import os

    for turn, (relative, text) in enumerate(files.items()):
        (pool / relative).parent.mkdir(parents=True, exist_ok=True)
        (pool / relative).write_text(text)
        at = 1_583_064_000 + turn * 3600
        os.utime(pool / relative, (at, at))


def _slashed(path: object) -> str:
    return str(path).replace("\\", "/")


def test_in_one_folder_an_acquisition_s_result_is_proposed_where_nothing_was_processed(
    tmp_path: Path,
) -> None:
    derived = _derived(
        tmp_path,
        {
            "loose/kelt_001.nd2": "x",
            "loose/kelt_001_results.csv": "a,b\n1,2\n",
            "loose/vorn_002.nd2": "x",
            "loose/vorn_002_processed.tif": "x",
            "loose/vorn_002_results.csv": "a,b\n1,2\n",
        },
    )
    # Three files of one folder are siblings: the processed image and the results, each named
    # for the movie, are each proposed from it. Across folders the results come from the processed
    # image (test_derivations_before_stages.py).
    assert derived == {
        ("loose/kelt_001.nd2", "loose/kelt_001_results.csv"),
        ("loose/vorn_002.nd2", "loose/vorn_002_processed.tif"),
        ("loose/vorn_002.nd2", "loose/vorn_002_results.csv"),
    }


#: One dated experiment whose chain is split across its stage subfolders, and inside it two
#: replicate days each split the same way; and a folder beneath no experiment split likewise.
_SPLIT_BY_STAGE = {
    "2020-02-21 zib kelt/raw/kelt_001.nd2": "x",
    "2020-02-21 zib kelt/processed/kelt_001_processed.tif": "x",
    "2020-02-21 zib kelt/analysis/kelt_001_results.csv": "a,b\n1,2\n",
    "2020-02-21 zib kelt/day1/raw/brisa_7.nd2": "x",
    "2020-02-21 zib kelt/day1/processed/brisa_7_processed.tif": "x",
    "2020-02-21 zib kelt/day2/raw/brisa_7.nd2": "x",
    "2020-02-21 zib kelt/day2/processed/brisa_7_processed.tif": "x",
    "2020-02-21 zib kelt/raw/tamen_7_raw.csv": "a,b\n1,2\n",
    "2020-02-21 zib kelt/processed/tamen_7_processed.csv": "a,b\n1,2\n",
    "2020-02-21 zib kelt/day1/raw/trell_8_raw.csv": "a,b\n1,2\n",
    "2020-02-21 zib kelt/day1/processed/trell_8_processed.csv": "a,b\n1,2\n",
    "2020-02-21 zib kelt/day2/processed/trell_8_processed.csv": "a,b\n1,2\n",
    "loose/raw/tamen_3_raw.csv": "a,b\n1,2\n",
    "loose/processed/tamen_3_processed.csv": "a,b\n1,2\n",
}


def test_an_experiment_s_chain_split_across_its_stage_subfolders_is_drawn_whole(
    tmp_path: Path,
) -> None:
    """The identity is compared within the experiment, across its stage subfolders.

    A stage subfolder's name is left out as a stage word is left out of a file's; any other folder
    between the experiment and the file keeps the identity apart, so two replicate days with the
    same names are never drawn together. A file beneath no experiment keeps its folder's scope.
    Where an acquisition names the files, its group proposes each from the acquisition. The
    results' link from the processed image is the identity's, the evidence joining the two across
    the experiment's folders. The acquisition's rule leaves the results' pair with the movie, so
    the chain stays link by link without a stage word.
    """
    experiment = "2020-02-21 zib kelt"
    named, identity = "derivation_named_for_an_acquisition", "derivation_within_name_identity"
    later = "same_file_later_step"
    assert _all_derivations(tmp_path, _SPLIT_BY_STAGE) == {
        (f"{experiment}/raw/kelt_001.nd2", f"{experiment}/processed/kelt_001_processed.tif", named),
        (
            f"{experiment}/processed/kelt_001_processed.tif",
            f"{experiment}/analysis/kelt_001_results.csv",
            identity,
        ),
        (
            f"{experiment}/day1/raw/brisa_7.nd2",
            f"{experiment}/day1/processed/brisa_7_processed.tif",
            named,
        ),
        (
            f"{experiment}/day2/raw/brisa_7.nd2",
            f"{experiment}/day2/processed/brisa_7_processed.tif",
            named,
        ),
        (
            f"{experiment}/raw/tamen_7_raw.csv",
            f"{experiment}/processed/tamen_7_processed.csv",
            identity,
        ),
        (
            f"{experiment}/day1/raw/trell_8_raw.csv",
            f"{experiment}/day1/processed/trell_8_processed.csv",
            identity,
        ),
        # Names differing only by a stage word are one file at two steps, proposed too.
        (
            f"{experiment}/raw/tamen_7_raw.csv",
            f"{experiment}/processed/tamen_7_processed.csv",
            later,
        ),
        (
            f"{experiment}/day1/raw/trell_8_raw.csv",
            f"{experiment}/day1/processed/trell_8_processed.csv",
            later,
        ),
    }


def test_the_identity_within_one_folder_is_left_as_it_was(tmp_path: Path) -> None:
    """``same_name_identity`` keys a class as it did at dbbb85b, so a person's verdicts hold.

    Two files of one folder sharing a name identity: one class, keyed on its components, the
    places of its numbers and its folder, and on nothing an experiment adds; the same two files are
    one class of the identity within an experiment too, keyed on the experiment instead.
    """
    import json

    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    for relative in (
        "2020-02-21 zib kelt/vorn_5_raw.csv",
        "2020-02-21 zib kelt/vorn_5_processed.csv",
    ):
        (pool / relative).parent.mkdir(parents=True, exist_ok=True)
        (pool / relative).write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.rule_id, c.object_reference, e.parent_entry_id FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id IN ('same_name_identity', 'same_name_identity_within_experiment') "
            "AND c.resolution_status = 'resolved'"
        ).fetchall()
    keys = {(str(rule), str(key)) for rule, key, _ in rows}
    assert len(rows) == 4 and len(keys) == 2
    folder = {str(parent) for _, _, parent in rows}.pop()
    by_rule = {rule: json.loads(key) for rule, key in keys}
    # dbbb85b's key: the components, the numbers' places, and the folder, in canonical JSON.
    assert by_rule["same_name_identity"] == {
        "components": [["number", "5"], ["word", "vorn"]],
        "ordered": [["number", "5"]],
        "folder": folder,
    }
    within = by_rule["same_name_identity_within_experiment"]
    assert within["experiment"] == folder and within["between"] == []
    assert "folder" not in within


# ---------------------------------------------------------------------------
# A file named for an acquisition.
# ---------------------------------------------------------------------------


def _candidates_of(tmp_path: Path, files: dict[str, str], rule: str) -> set[tuple[str, str]]:
    """Index a pool, infer it under revision 4, and return one proposing rule's pairs."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    _write_in_turn(pool, files)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT s.relative_path, t.relative_path FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id WHERE c.rule_id = ?",
            [rule],
        ).fetchall()
    return {(_slashed(source), _slashed(target)) for source, target in rows}


def test_an_analysis_named_for_an_acquisition_is_proposed_from_that_acquisition(
    tmp_path: Path,
) -> None:
    """A tool names what it writes after the movie it read, with words of its own beside it.

    The identity cannot join them, since the tool's words are not stage words; the name held whole
    can. Two replicate days holding one movie name each keep their own: an output is drawn to the
    acquisition nearest it, and where two are equally near, to neither.
    """
    experiment = "2021-05-06 brisa trell"
    derived = _candidates_of(
        tmp_path,
        {
            f"{experiment}/raw/brisa_trell_001.nd2": "x",
            f"{experiment}/analysis/zorp_brisa_trell_001_results.csv": "a,b\n1,2\n",
            f"{experiment}/day1/kelt_movie_002.nd2": "x",
            f"{experiment}/day2/kelt_movie_002.nd2": "x",
            f"{experiment}/day1/tracks/kelt_movie_002_zorp_results.csv": "a,b\n1,2\n",
            f"{experiment}/pooled_kelt_movie_002_results.csv": "a,b\n1,2\n",
        },
        "derivation_named_for_an_acquisition",
    )
    assert derived == {
        (
            f"{experiment}/raw/brisa_trell_001.nd2",
            f"{experiment}/analysis/zorp_brisa_trell_001_results.csv",
        ),
        (
            f"{experiment}/day1/kelt_movie_002.nd2",
            f"{experiment}/day1/tracks/kelt_movie_002_zorp_results.csv",
        ),
    }


def test_a_pair_both_readings_join_is_proposed_once_by_the_acquisition_s_group(
    tmp_path: Path,
) -> None:
    """Where both ends are named for one acquisition, that group's derivation proposes the pair.

    The two readings overlap wholly on the evaluation generator, so each derivation would stand on
    the Candidates page twice. The acquisition's group holds every file named for it, a tool's own
    words and all. Two files of one folder named for it are siblings, each from the acquisition.
    The identity proposes nothing within one folder, so the raw and processed tables beside them
    are siblings too.
    """
    files = {
        "loose/kelt_001.nd2": "x",
        "loose/kelt_001_results.csv": "a,b\n1,2\n",
        "loose/zorp_kelt_001_tracks.csv": "a,b\n1,2\n",
        "loose/vorn_table_raw.csv": "a,b\n1,2\n",
        "loose/vorn_table_processed.csv": "a,b\n1,2\n",
    }
    assert _all_derivations(tmp_path, files) == {
        ("loose/kelt_001.nd2", "loose/kelt_001_results.csv", "derivation_named_for_an_acquisition"),
        (
            "loose/kelt_001.nd2",
            "loose/zorp_kelt_001_tracks.csv",
            "derivation_named_for_an_acquisition",
        ),
        # The identity proposes nothing within one folder, but the two tables are one file at two
        # steps, and the later step is proposed as made from the earlier.
        ("loose/vorn_table_raw.csv", "loose/vorn_table_processed.csv", "same_file_later_step"),
    }


# ---------------------------------------------------------------------------
# Direction, cost, and one pair proposed once.
# ---------------------------------------------------------------------------

_FULL_CHAIN = {
    "raw": "processed",
    "acquisition": "processed",
    "processed": "analysis",
    "analysis": "quantification",
    "quantification": "summary",
}


def test_what_a_class_holds_is_read_once_per_class_not_once_per_pair(monkeypatch) -> None:
    """A large class is not slow, because no pair re-reads every member.

    The named-for derivation reads its class, for its chain by time, once per class, and each
    end of a pair once."""
    from ingence.rulebook.predicates import classify

    predicate = PREDICATES.get("derives_from_named_acquisition")
    key = '{"acquisition":"vorn.nd2"}'

    def member(entry_id: str, hour: int) -> EntryRecords:
        records = _file(entry_id)
        claim = _stage(entry_id, "x").model_copy(
            update={
                "claim_id": f"member:{entry_id}",
                "rule_id": "named_for_an_acquisition",
                "predicate": "groups.named_for_an_acquisition",
                "object_reference": key,
            }
        )
        entry = records.entry.model_copy(
            update={"modified_ns": (1_583_064_000 + hour * 3600) * 10**9}
        )
        return EntryRecords(entry=entry, claims=(claim,))

    members = (
        member("vorn.nd2", 0),
        *(member(f"vorn_{index}_results.csv", 1) for index in range(200)),
    )
    read = 0
    original = classify.earliest_credible_instant

    def counted(entry: object) -> object:
        nonlocal read
        read += 1
        return original(entry)  # type: ignore[arg-type]

    monkeypatch.setattr(classify, "earliest_credible_instant", counted)
    parameters = {"link_by_link_within": "same_name_identity_within_experiment"}
    for target in members[1:]:
        pair = PairRecords(
            source=members[0],
            target=target,
            within="named_for_an_acquisition",
            source_membership="member:vorn.nd2",
            target_membership=f"member:{target.entry.entry_id}",
            members=members,
        )
        assert predicate.evaluate(pair, parameters) is not None
    # Two ends a pair, and the class once: never pairs times members.
    assert read <= 2 * (len(members) - 1) + len(members)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "a file named for an acquisition is made from it, and the middle link of this chain "
        "needs evidence beyond a name and a time; the processed image's name holds a tool's word "
        "the results' does not, so the two form no name identity and nothing joins them: both are "
        "proposed from the movie"
    ),
)
def test_a_chain_split_between_two_groupings_is_proposed_link_by_link_once(
    tmp_path: Path,
) -> None:
    """The chain this test asserts, link by link, is not what the engine proposes.

    The test expects the acquisition to the processed file, and the processed file to the results.
    The processed file carries a tool's word that the identity cannot join, so the identity holds
    no processed file. Only stage words could give the middle link, and stage words direct no
    derivation. So the engine proposes both files from the acquisition, and the ``xfail`` reason
    says why.
    """
    experiment = "2020-03-04 vorn kelt"
    files = {
        f"{experiment}/raw/kelt_001.nd2": "x",
        f"{experiment}/processed/zorp_kelt_001_processed.tif": "x",
        f"{experiment}/analysis/kelt_001_results.csv": "a,b\n1,2\n",
    }
    everything = _all_derivations(tmp_path, files)
    assert everything == {
        (
            f"{experiment}/raw/kelt_001.nd2",
            f"{experiment}/processed/zorp_kelt_001_processed.tif",
            "derivation_named_for_an_acquisition",
        ),
        (
            f"{experiment}/processed/zorp_kelt_001_processed.tif",
            f"{experiment}/analysis/kelt_001_results.csv",
            "derivation_named_for_an_acquisition",
        ),
    }


def _all_derivations(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str]]:
    """Index a pool, infer it under revision 4, and return every derivation with its rule."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    _write_in_turn(pool, files)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT s.relative_path, t.relative_path, c.rule_id FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id WHERE c.primitive = 'derives'"
        ).fetchall()
    return {(_slashed(source), _slashed(target), str(rule)) for source, target, rule in rows}


def _plate(entry_id: str, name: str, *, acquisition: bool, compound: str = "") -> EntryRecords:
    """One file in one folder, an acquisition where said, as ``acquisitions_named`` reads it."""
    extension = name.rsplit(".", 1)[-1]
    claims = ()
    if acquisition:
        claims = (
            Claim(
                claim_id=f"acq:{entry_id}",
                run_id="r",
                subject=entry_id,
                predicate="classifies.acquisition_stage_from_format",
                object_reference="acquisition",
                rule_id="acquisition_stage_from_format",
                rule_version="1",
                scope=PROJECT_SCOPE,
                defeaters=(Defeater(reason="a test", rule_id="d", rule_version="1"),),
                resolution_status=ResolutionStatus.RESOLVED,
            ),
        )
    return EntryRecords(
        entry=Entry(
            entry_id=entry_id,
            root_id="root",
            relative_path=f"zib/{name}",
            name=name,
            depth=2,
            entry_type="file",
            extension=extension,
            compound_extension=compound or extension,
            first_seen_run="r",
            last_seen_run="r",
            parent_entry_id="folder:zib",
        ),
        claims=claims,
    )


def test_a_compound_extension_is_no_part_of_an_acquisition_s_name() -> None:
    """``kelt_001.ome.tif`` does not keep ``ome`` in its name, so the name still names the movie."""
    from ingence.infer.experiment_places import acquisitions_named

    named = acquisitions_named(
        [
            _plate("a", "kelt_001.ome.tif", acquisition=True, compound="ome.tif"),
            _plate("m", "kelt_001_mask.tif", acquisition=False),
        ],
        {},
    )
    assert named["m"].acquisition == "a"


def test_naming_files_for_acquisitions_grows_with_the_files_not_their_square() -> None:
    """An index by a name's first part does not compare every file with every acquisition.

    Such a comparison is slow: 4,000 acquisitions of one plate took 12.85 s."""
    import time

    from ingence.infer.experiment_places import acquisitions_named

    def seconds(count: int) -> float:
        pool = []
        for index in range(count):
            pool.append(_plate(f"a{index}", f"plate_{index:05d}.nd2", acquisition=True))
            pool.extend(
                _plate(f"f{index}_{j}", f"plate_{index:05d}_out{j}.csv", acquisition=False)
                for j in range(2)
            )
        # The best of three: one timing on a loaded runner read 0.28 s against a bound drawn from a
        # small pool's 0.008 s, a scheduler's noise and not the square.
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            named = acquisitions_named(pool, {})
            best = min(best, time.perf_counter() - started)
            assert len(named) == 3 * count
        return best

    small, large = seconds(500), seconds(4000)
    # Eight times the files: linear work is about eight times the time, the square sixty-four.
    assert large < 24 * max(small, 0.02), (small, large)


def test_the_named_for_grouping_is_read_by_its_declared_shape_and_its_own_minimum() -> None:
    """Each rule keeps its own minimum.

    The pass must not take the first rule found by its predicate's name and read that rule's
    minimum for every rule. The predicate declares what it reads, the pass hands the matches of
    the smallest minimum any such rule names, and each rule keeps its own."""
    from ingence.infer.subjects import NamedAcquisition
    from ingence.rulebook.registry import Reads, reads_of

    declaration = PREDICATES.declaration("named_for_an_acquisition")
    assert reads_of(declaration) is Reads.NAMED_ACQUISITION
    predicate = PREDICATES.get("named_for_an_acquisition")
    held = EntryRecords(
        entry=_entry("kelt_0001_results.csv"),
        named_acquisition=NamedAcquisition(acquisition="kelt_0001.nd2", claim="c", characters=8),
    )
    subject = held.narrowed(Reads.NAMED_ACQUISITION)
    assert predicate.evaluate(subject, {"minimum_key_characters": 6}) is not None
    assert predicate.evaluate(subject, {"minimum_key_characters": 12}) is None
