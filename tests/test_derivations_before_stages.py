"""Derivations found from their own evidence, before any stage is read.

All derivations come before stage assignment, and a stage is never a central part of what a file
is. So:

- Each evidence gives its own direction. A file named for an acquisition is made from it. A
  script's calls read one file and write the other. A name identity and a describing file naming
  both give none: the earliest credible time orders the pair, and with no time the pair stays
  undirected, proposed as ``concerns`` ("Related").
- No stage gates which pairs are formed. A pair is formed only where a key joins its ends,
  looked up: a file named for an acquisition is paired with that acquisition, never with the other
  files named for it.
- The bound never drops a supported derivation where each subject's key offers few: the invented
  group of 4,000 files named for one acquisition proposes all 400 analyses.
- A stage step corroborates a derivation and never creates one. Where a derivation and a name
  disagree about a stage, a finding asks the person.

Every name here is invented.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.project import Project
from ingence.core.schema import Claim, Defeater, Entry
from ingence.core.tiers import ResolutionStatus
from ingence.infer.subjects import EntryRecords, PairRecords
from ingence.record.verdicts import CANDIDATE_WORDS
from ingence.rulebook.load import load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES
from ingence.rulebook.resolve import effective_rulebook
from ingence.rulebook.revision_4 import REVISION
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.infer.run import InferenceReport
    from ingence.rulebook.resolve import EffectiveRulebook

load_builtins()

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000001Z_444444444444"
FIRST = "run_20260101T000002Z_222222222222"
NAMED_FOR = "derivation_named_for_an_acquisition"
IDENTITY = "derivation_within_name_identity"
RELATED_IDENTITY = "related_within_name_identity"
DESCRIBED = "derivation_named_by_a_describing_file"
RELATED_DESCRIBED = "related_by_a_describing_file"
CONFLICT = "stage_disagrees_with_a_derivation"
ACQUISITION = "zephvorn001"
#: An hour, in seconds: far beyond the ordering's tolerance.
HOUR = 3600
#: A fixed instant for the files' times, 1 March 2020 at noon UTC, as seconds since the epoch.
NOON = 1_583_064_000


def _book(tmp_path: Path) -> EffectiveRulebook:
    """The rules in force: the shipped book, revision 4 among its revisions."""
    del tmp_path
    return effective_rulebook(load_revisions())


def _write(pool: Path, files: dict[str, str], times: dict[str, int] | None = None) -> None:
    for relative, text in files.items():
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        at = (times or {}).get(relative, NOON)
        os.utime(path, (at, at))


def _project(tmp_path: Path, pool: Path) -> Project:
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    return project


def _infer(project: Project, tmp_path: Path) -> InferenceReport:
    from ingence.infer.run import run_inference

    return run_inference(
        project, run_id=FIRST, started_at="2026-01-01T00:00:02Z", rulebook=_book(tmp_path)
    )


def _candidates(project: Project) -> list[tuple[str, str, str, str, list[str]]]:
    """Each candidate as (rule, source name, target name, primitive, support names)."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.rule_id, s.name, t.name, c.primitive, c.support_json FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id JOIN entries t ON t.entry_id = c.target_id "
            "ORDER BY 1, 2, 3"
        ).fetchall()
    return [
        (rule, source, target, primitive, [item["name"] for item in json.loads(support)])
        for rule, source, target, primitive, support in rows
    ]


def _tally(report: InferenceReport, rule_id: str):  # type: ignore[no-untyped-def]
    return {tally.rule_id: tally for tally in report.proposals}[rule_id]


def _findings(project: Project, rule_id: str) -> list[tuple[str, str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, f.reason FROM findings f JOIN entries e ON e.entry_id = f.subject "
            "WHERE f.rule_id = ? ORDER BY 1",
            [rule_id],
        ).fetchall()
    return [(str(name), str(reason)) for name, reason in rows]


def _counting_pairs(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count, per proposing rule, the pairs handed to its predicate."""
    import ingence.infer.candidates as candidates

    counted: dict[str, int] = {}
    original = candidates.support_for_pair

    def spy(rule, predicate, pair):  # type: ignore[no-untyped-def]
        counted[rule.rule_id] = counted.get(rule.rule_id, 0) + 1
        return original(rule, predicate, pair)

    monkeypatch.setattr(candidates, "support_for_pair", spy)
    return counted


def _named_pool(tmp_path: Path, unstaged: int, analysed: int = 0) -> Path:
    """One experiment: an acquisition, files named for it stating no stage, and analyses."""
    pool = tmp_path / "pool"
    experiment = pool / "2020-02-21 brisa kelt"
    (experiment / "raw").mkdir(parents=True)
    (experiment / "raw" / f"{ACQUISITION}.nd2").write_bytes(b"\0" * 16)
    for index in range(unstaged):
        folder = experiment / f"tiles{index % 7}"
        folder.mkdir(exist_ok=True)
        (folder / f"{ACQUISITION}_tile{index:04d}.txt").write_text("x\n")
    for index in range(analysed):
        (experiment / f"{ACQUISITION}_vorn{index}_analysis.csv").write_text("x,y\n1,2\n")
    return pool


# ---------------------------------------------------------------------------
# The acquisition a file is named for: its own direction, looked up.
# ---------------------------------------------------------------------------


def test_every_file_named_for_an_acquisition_is_proposed_as_made_from_it_without_a_stage(
    tmp_path, monkeypatch
) -> None:
    """Three files stating no stage are proposed.

    The name held whole is the evidence and says the direction. A pair does not need a stage at
    both ends."""
    project = _project(tmp_path, _named_pool(tmp_path, unstaged=3, analysed=2))
    counted = _counting_pairs(monkeypatch)

    report = _infer(project, tmp_path)

    named = [row for row in _candidates(project) if row[0] == NAMED_FOR]
    assert len(named) == 5
    assert {source for _, source, _, _, _ in named} == {f"{ACQUISITION}.nd2"}
    assert {primitive for _, _, _, primitive, _ in named} == {"derives"}
    # Each pair formed joins a file to the acquisition its name holds, and nothing else.
    assert counted[NAMED_FOR] == _tally(report, NAMED_FOR).formed == 5


def test_a_group_forms_only_the_pairs_its_key_joins_never_its_square(tmp_path, monkeypatch) -> None:
    """Twenty files named for one acquisition: twenty pairs, each a file and its acquisition.

    The group does not form its square, 380 ordered pairs. The files named for one acquisition
    are joined to it by the name, and to one another by nothing, so no pair between two of them
    is formed.
    """
    project = _project(tmp_path, _named_pool(tmp_path, unstaged=19))
    counted = _counting_pairs(monkeypatch)

    report = _infer(project, tmp_path)

    tally = _tally(report, NAMED_FOR)
    assert tally.pairs == tally.formed == counted[NAMED_FOR] == 19
    assert tally.truncated == 0


def test_the_invented_group_of_four_thousand_files_proposes_all_four_hundred_analyses(
    tmp_path,
) -> None:
    """Each file looks up its one acquisition, so all 400 analyses are proposed.

    The acquisition's 3,999 counterparts are never cut to 25 by identifier."""
    project = _project(tmp_path, _named_pool(tmp_path, unstaged=3599, analysed=400))

    report = _infer(project, tmp_path)

    named = [row for row in _candidates(project) if row[0] == NAMED_FOR]
    analyses = [row for row in named if row[2].endswith("_analysis.csv")]
    assert len(analyses) == 400
    assert len(named) == 3999
    assert _tally(report, NAMED_FOR).truncated == 0


def test_a_chain_named_for_one_acquisition_is_proposed_link_by_link_by_time_not_stage(
    tmp_path,
) -> None:
    """An acquisition, its processed image and the results from that image, all named for the
    acquisition: the acquisition to the image and the image to the results, never the acquisition
    straight to the results, which the image made between them. The image's time between the
    two, in the results' own name identity, decides this."""
    pool = tmp_path / "pool"
    experiment = "2020-02-21 brisa kelt"
    _write(
        pool,
        {
            f"{experiment}/raw/vorn_kelt_001.nd2": "x",
            f"{experiment}/processed/vorn_kelt_001_processed.tif": "x",
            f"{experiment}/analysis/vorn_kelt_001_results.csv": "a,b\n1,2\n",
        },
        {
            f"{experiment}/raw/vorn_kelt_001.nd2": NOON,
            f"{experiment}/processed/vorn_kelt_001_processed.tif": NOON + HOUR,
            f"{experiment}/analysis/vorn_kelt_001_results.csv": NOON + 2 * HOUR,
        },
    )
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    derived = {
        (rule, source, target)
        for rule, source, target, primitive, _ in _candidates(project)
        if primitive == "derives"
    }
    # The processed image and the results share a name identity across the experiment's folders,
    # the evidence that joins them; the acquisition's rule leaves the results' pair with the movie.
    assert derived == {
        (NAMED_FOR, "vorn_kelt_001.nd2", "vorn_kelt_001_processed.tif"),
        (IDENTITY, "vorn_kelt_001_processed.tif", "vorn_kelt_001_results.csv"),
    }


# ---------------------------------------------------------------------------
# A name identity: the earliest credible time orders the pair, or it stays undirected.
# ---------------------------------------------------------------------------


#: One experiment, its stage subfolders: a name identity's evidence is across its folders.
EXPERIMENT = "2020-02-21 brisa kelt"


def _identity_pool(tmp_path: Path, raw_at: int, analysis_at: int) -> Path:
    pool = tmp_path / "pool"
    raw, analysis = (
        f"{EXPERIMENT}/raw/vorn_001_raw.tif",
        f"{EXPERIMENT}/analysis/vorn_001_analysis.csv",
    )
    _write(pool, {raw: "x", analysis: "a,b\n1,2\n"}, {raw: raw_at, analysis: analysis_at})
    return pool


def test_an_identity_s_pair_is_ordered_by_the_earlier_file_and_a_stage_step_corroborates(
    tmp_path,
) -> None:
    project = _project(tmp_path, _identity_pool(tmp_path, NOON, NOON + HOUR))

    _infer(project, tmp_path)

    rows = [row for row in _candidates(project) if row[0] in (IDENTITY, RELATED_IDENTITY)]
    assert [(rule, source, target, primitive) for rule, source, target, primitive, _ in rows] == [
        (IDENTITY, "vorn_001_raw.tif", "vorn_001_analysis.csv", "derives")
    ]
    assert "stage_step" in rows[0][4]
    assert _findings(project, CONFLICT) == []


def test_a_derivation_the_clock_orders_against_the_names_stages_is_proposed_and_asked_about(
    tmp_path,
) -> None:
    """The analysis is the older file: the time gives the direction, the stage words never do,
    and the name stating raw on a file made from another is a conflict a person is shown."""
    project = _project(tmp_path, _identity_pool(tmp_path, NOON + HOUR, NOON))

    _infer(project, tmp_path)

    rows = [row for row in _candidates(project) if row[0] in (IDENTITY, RELATED_IDENTITY)]
    assert [(source, target, primitive) for _, source, target, primitive, _ in rows] == [
        ("vorn_001_analysis.csv", "vorn_001_raw.tif", "derives")
    ]
    assert "stage_step" not in rows[0][4]
    raised = _findings(project, CONFLICT)
    assert [name for name, _ in raised] == ["vorn_001_raw.tif"]
    assert "vorn_001_analysis.csv" in raised[0][1]


def test_an_identity_s_pair_no_clock_orders_is_proposed_once_as_related(tmp_path) -> None:
    project = _project(tmp_path, _identity_pool(tmp_path, NOON, NOON))

    _infer(project, tmp_path)

    rows = [row for row in _candidates(project) if row[0] in (IDENTITY, RELATED_IDENTITY)]
    assert [(rule, primitive) for rule, _, _, primitive, _ in rows] == [
        (RELATED_IDENTITY, "concerns")
    ]
    assert _findings(project, CONFLICT) == []


# ---------------------------------------------------------------------------
# A describing file naming both ends: no direction of its own.
# ---------------------------------------------------------------------------


def _described(tmp_path: Path, times: dict[str, int]) -> Project:
    from ingence.core.settings import ExtractionSettings
    from ingence.scan.deepen import run_deepen

    pool = tmp_path / "pool"
    named = ["brisa_plate.csv", "kelt_counts.csv"]
    _write(
        pool,
        {
            "vorn/notes_zeph.py": "".join(
                f'FILE_{at} = "data/{name}"\n' for at, name in enumerate(named)
            ),
            **{f"vorn/data/{name}": "well,value\nA1,1\n" for name in named},
        },
        times,
    )
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    project = project.save_settings(
        project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    run_deepen(project, run_id=DIVE, started_at="2026-01-01T00:00:01Z")
    return project


def test_two_files_a_script_names_at_different_times_are_related_and_neither_made_from_the_other(
    tmp_path,
) -> None:
    """Before, a describing file's pair needed a stage step for its direction, so two tables
    stating no stage were never proposed. A mention says neither which was read nor which written,
    and two inputs of one script were made in turn and neither from the other, so revision 4 orders
    no describing file's pair by time: the two are proposed once, as related."""
    project = _described(
        tmp_path, {"vorn/data/kelt_counts.csv": NOON, "vorn/data/brisa_plate.csv": NOON + HOUR}
    )

    _infer(project, tmp_path)

    rows = [row for row in _candidates(project) if row[0] in (DESCRIBED, RELATED_DESCRIBED)]
    assert [(rule, {source, target}, primitive) for rule, source, target, primitive, _ in rows] == [
        (RELATED_DESCRIBED, {"brisa_plate.csv", "kelt_counts.csv"}, "concerns")
    ]


def test_two_files_a_script_names_at_one_time_are_proposed_once_as_related(tmp_path) -> None:
    project = _described(tmp_path, {})

    _infer(project, tmp_path)

    rows = [row for row in _candidates(project) if row[0] in (DESCRIBED, RELATED_DESCRIBED)]
    assert [(rule, primitive) for rule, _, _, primitive, _ in rows] == [
        (RELATED_DESCRIBED, "concerns")
    ]


# ---------------------------------------------------------------------------
# The predicates, one pair at a time.
# ---------------------------------------------------------------------------


def _entry(entry_id: str, modified: int | None = None) -> Entry:
    return Entry(
        entry_id=entry_id,
        root_id="root",
        relative_path=f"kelt/{entry_id}",
        name=entry_id,
        depth=1,
        entry_type="file",
        modified_ns=None if modified is None else modified * 1_000_000_000,
        first_seen_run=CENSUS,
        last_seen_run=CENSUS,
    )


def _claim(subject: str, rule_id: str, value: str) -> Claim:
    return Claim(
        claim_id=f"claim:{subject}:{rule_id}:{value}",
        run_id="r",
        subject=subject,
        predicate=f"classifies.{rule_id}",
        object_reference=value,
        rule_id=rule_id,
        rule_version="1",
        scope=PROJECT_SCOPE,
        defeaters=(Defeater(reason="a test defeater", rule_id="defeater", rule_version="1"),),
        resolution_status=ResolutionStatus.RESOLVED,
    )


def _file(entry_id: str, *stages: str, modified: int | None = None) -> EntryRecords:
    return EntryRecords(
        entry=_entry(entry_id, modified),
        claims=tuple(_claim(entry_id, "pipeline_stage_from_name", stage) for stage in stages),
    )


def test_the_stage_step_predicate_proposes_nothing_whatever_the_stages() -> None:
    predicate = PREDICATES.get("derives_from_stage_transition")
    pair = PairRecords(
        source=_file("kelt_001.nd2", "acquisition"),
        target=_file("kelt_001_processed.tif", "processed"),
        within="same_name_identity",
        source_membership="a",
        target_membership="b",
    )
    parameters = {"stage_transitions": {"acquisition": "processed"}}
    assert predicate.evaluate(pair, parameters) is None
    assert "never creates" in predicate.explain_abstention(pair, parameters).reason


def test_an_identity_pair_within_the_tolerance_is_undirected_and_beyond_it_ordered() -> None:
    predicate = PREDICATES.get("derives_within_a_name_identity")
    parameters = {"direction": "earliest_first", "tolerance_seconds": 60}
    undirected = {**parameters, "direction": "undirected"}

    def pair(first: int, second: int) -> PairRecords:
        return PairRecords(
            source=_file("brisa_7.csv", modified=first),
            target=_file("brisa_7.tif", modified=second),
            within="same_name_identity_within_experiment",
            source_membership="a",
            target_membership="b",
        )

    assert predicate.evaluate(pair(NOON, NOON + 30), parameters) is None
    assert predicate.evaluate(pair(NOON, NOON + 30), undirected) is not None
    assert predicate.evaluate(pair(NOON, NOON + 120), parameters) is not None
    assert predicate.evaluate(pair(NOON, NOON + 120), undirected) is None
    assert predicate.evaluate(pair(NOON + 120, NOON), parameters) is None
    # An undirected pair is proposed once, from the end whose identifier sorts first.
    flipped = pair(NOON, NOON)
    flipped = PairRecords(
        source=flipped.target,
        target=flipped.source,
        within=flipped.within,
        source_membership="b",
        target_membership="a",
    )
    assert predicate.evaluate(flipped, undirected) is None
    # The clock orders the pair and supports nothing: its component weighs zero.
    support = predicate.evaluate(pair(NOON, NOON + 120), parameters)
    assert {component.family: component.value for component in support}["direction"] == 0.0
    # A rule saying its evidence is never ordered by time reads every pair as unordered.
    unordered = {"ordered_by_time": False, "tolerance_seconds": 60}
    assert predicate.evaluate(pair(NOON, NOON + 120), {**unordered, **{"direction": "x"}}) is None
    assert predicate.evaluate(pair(NOON, NOON + 120), {**unordered, "direction": "undirected"})


def test_the_revised_endpoint_constraint_refuses_a_program_and_reads_no_stage() -> None:
    rules = {rule["id"]: rule for rule in REVISION["rules"]}  # type: ignore[attr-defined]
    rule = rules["derivation_endpoints_hold_roles_or_a_scripts_calls"]
    # A changed rule keeps its identifier at a new version.
    assert (rule["version"], rule["predicate"]) == ("2", "endpoint_is_not_of_an_excluded_class")
    predicate = PREDICATES.get(rule["predicate"])
    parameters = {spec["name"]: spec["default"] for spec in rule["parameters"]}

    def end(entry_id: str, *classes: str) -> EntryRecords:
        return EntryRecords(
            entry=_entry(entry_id),
            claims=tuple(_claim(entry_id, "program_from_extension", value) for value in classes),
        )

    def pair(source: EntryRecords, target: EntryRecords) -> PairRecords:
        return PairRecords(
            source=source, target=target, within="w", source_membership="a", target_membership="b"
        )

    assert predicate.evaluate(pair(end("zeph.tif"), end("zeph.csv")), parameters) is None
    refused = predicate.evaluate(pair(end("zeph.tif"), end("zeph.py", "python_source")), parameters)
    assert refused is not None and "python_source" in refused.reason


# ---------------------------------------------------------------------------
# "Related" is the answer an undirected pair takes.
# ---------------------------------------------------------------------------


def test_related_confirms_a_pair_proposed_as_related_and_rejects_one_proposed_as_derived(
    tmp_path,
) -> None:
    """Before, "related" on any pair was written as a rejection of a derivation; on a pair
    proposed as related (``concerns``) it is that proposal confirmed."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.verdicts import candidate_subjects, current_run, write_verdict

    kinds = {}
    for name, analysis_at in (("same", NOON), ("later", NOON + HOUR)):
        root = tmp_path / name
        root.mkdir()
        project = _project(root, _identity_pool(root, NOON, analysis_at))
        _infer(project, root)
        with open_catalog(project.path) as catalog:
            ((candidate_id, primitive),) = catalog.query(
                "SELECT candidate_id, primitive FROM candidates WHERE rule_id IN (?, ?)",
                [IDENTITY, RELATED_IDENTITY],
            ).fetchall()
            subject = candidate_subjects(catalog, [candidate_id])[candidate_id]
            run_id = current_run(catalog)
        event = write_verdict(
            project.path,
            subject,
            "related",
            actor="ines",
            occurred_at="2026-09-30T23:00:00Z",
            against_run_id=run_id,
        )
        kinds[str(primitive)] = event.kind
        assert json.loads(event.payload)["relation"] == "concerns"
    assert kinds == {"concerns": RecordEventKind.CONFIRM, "derives": RecordEventKind.REJECT}


def test_only_a_rule_after_proposing_may_read_the_derivations() -> None:
    """Classifying and grouping run before the derivations exist, so a rule of either effect
    reading them would always be handed none and look like a rule that found nothing; the loader
    refuses it. A flag runs after proposing and reads them."""
    from ingence.core.schema import RuleEffect
    from ingence.rulebook.registry import READS_BY_EFFECT, Reads

    assert Reads.DERIVED_FROM not in READS_BY_EFFECT[RuleEffect.CLASSIFIES]
    assert Reads.DERIVED_FROM not in READS_BY_EFFECT[RuleEffect.GROUPS]
    assert Reads.DERIVED_FROM in READS_BY_EFFECT[RuleEffect.FLAGS]


# ---------------------------------------------------------------------------
# Chains link by link, siblings, clocks, and few questions.
# ---------------------------------------------------------------------------


def _derived_in(project: Project, *rules: str) -> set[tuple[str, str, str]]:
    return {
        (rule, source, target)
        for rule, source, target, primitive, _ in _candidates(project)
        if primitive == "derives" and (not rules or rule in rules)
    }


def _raised(project: Project, rule_id: str) -> list[tuple[str, str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, u.value FROM findings f, unnest(f.raised) AS u(value) "
            "JOIN entries e ON e.entry_id = f.subject WHERE f.rule_id = ? ORDER BY 1, 2",
            [rule_id],
        ).fetchall()
    return [(str(name), str(value)) for name, value in rows]


def test_an_identity_s_chain_across_folders_is_proposed_link_by_link_never_its_closure(
    tmp_path,
) -> None:
    """Raw, processed and analysis of one identity, made an hour apart in three folders: two
    derivations, never the raw file straight to the analysis the processed file was made between."""
    files = {
        f"{EXPERIMENT}/raw/vorn_001_raw.tif": NOON,
        f"{EXPERIMENT}/processed/vorn_001_processed.tif": NOON + HOUR,
        f"{EXPERIMENT}/analysis/vorn_001_analysis.csv": NOON + 2 * HOUR,
    }
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    assert _derived_in(project, IDENTITY) == {
        (IDENTITY, "vorn_001_raw.tif", "vorn_001_processed.tif"),
        (IDENTITY, "vorn_001_processed.tif", "vorn_001_analysis.csv"),
    }


def test_two_files_of_one_folder_sharing_an_identity_are_siblings_and_proposed_as_nothing(
    tmp_path,
) -> None:
    """Two outputs in one folder sharing an identity are siblings.

    An hour between them makes neither the other's source, and not a related pair either."""
    pool = _identity_pool(tmp_path, NOON, NOON + HOUR)
    files = {"kelt/vorn_002_raw.tif": NOON, "kelt/vorn_002_analysis.csv": NOON + HOUR}
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    pairs = {
        frozenset((source, target))
        for rule, source, target, _, _ in _candidates(project)
        if rule in (IDENTITY, RELATED_IDENTITY)
    }
    assert pairs == {frozenset(("vorn_001_raw.tif", "vorn_001_analysis.csv"))}


def test_files_named_for_one_acquisition_are_each_made_from_it_and_never_one_from_another(
    tmp_path,
) -> None:
    """A file named for an acquisition is made from the acquisition, its name's evidence.

    It is never made from another output of the acquisition by time alone. Two outputs are
    siblings unless other evidence joins them.

    The cases, each in its own experiment: a movie's processed image and results in two folders an
    hour apart, names forming no identity (a middle link needs evidence beyond a name and a time); a
    mask and a tracking file a day apart in two folders; two results of one rung in two folders; and
    a movie's tiles written a minute and a half apart into seven folders."""
    files: dict[str, int] = {
        "2020-03-05 vorn kelt/raw/kelt007.nd2": NOON,
        "2020-03-05 vorn kelt/processed/kelt007_processed.tif": NOON + HOUR,
        "2020-03-05 vorn kelt/analysis/kelt007_results.csv": NOON + 2 * HOUR,
        "2020-03-06 brisa kelt/raw/brisa008.nd2": NOON,
        "2020-03-06 brisa kelt/masks/brisa008_mask.tif": NOON + HOUR,
        "2020-03-06 brisa kelt/tracks/brisa008_tracks.csv": NOON + 25 * HOUR,
        "2020-03-07 brisa kelt/raw/brisa009.nd2": NOON,
        "2020-03-07 brisa kelt/tracking/brisa009_results_processed.csv": NOON + HOUR,
        "2020-03-07 brisa kelt/filtered/brisa009_results_filtered.csv": NOON + 2 * HOUR,
        "2020-03-08 zeph kelt/raw/zeph003.nd2": NOON,
    }
    for index in range(21):
        files[f"2020-03-08 zeph kelt/tiles{index % 7}/zeph003_tile{index:04d}.txt"] = (
            NOON + HOUR + index * 90
        )
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    derived = _derived_in(project)
    acquisitions = {"kelt007.nd2", "brisa008.nd2", "brisa009.nd2", "zeph003.nd2"}
    assert {source for _, source, _ in derived} == acquisitions
    assert {rule for rule, _, _ in derived} == {NAMED_FOR}
    assert len(derived) == 2 + 2 + 2 + 21


def test_a_file_named_for_an_acquisition_but_made_before_it_says_so_on_the_pair(
    tmp_path,
) -> None:
    """A plan named for a movie, written an hour before the movie, is proposed by its name, and the
    clock that disagrees is said on the pair itself, where a person's answer settles the direction,
    never as a question its answer could not decide."""
    experiment = "2020-03-06 mirdo kelt"
    files = {f"{experiment}/raw/mirdo005.nd2": NOON + HOUR, f"{experiment}/mirdo005_plan.txt": NOON}
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    (row,) = [row for row in _candidates(project) if row[0] == NAMED_FOR]
    assert (row[1], row[2]) == ("mirdo005.nd2", "mirdo005_plan.txt")
    assert "made_before_its_source" in row[4]


def test_a_file_of_one_identity_is_never_left_without_a_source_by_a_sibling(tmp_path) -> None:
    """A raw image, then a processed image and its analysis in one folder. The two of one
    folder are siblings, so the processed image never stands between the raw image and the
    analysis: each is proposed from the raw image."""
    files = {
        f"{EXPERIMENT}/raw/vorn_002_raw.tif": NOON,
        f"{EXPERIMENT}/analysis/vorn_002_processed.tif": NOON + HOUR,
        f"{EXPERIMENT}/analysis/vorn_002_analysis.csv": NOON + 2 * HOUR,
    }
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    assert _derived_in(project, IDENTITY) == {
        (IDENTITY, "vorn_002_raw.tif", "vorn_002_processed.tif"),
        (IDENTITY, "vorn_002_raw.tif", "vorn_002_analysis.csv"),
    }


def test_like_stage_conflicts_in_one_folder_are_one_question(tmp_path) -> None:
    """Several thousand files must not raise one question each.

    Two raw files each made after the analysis of its identity, in one folder, are one class and
    raise one question."""
    files: dict[str, int] = {}
    for sample in ("vorn_001", "vorn_002"):
        files[f"{EXPERIMENT}/analysis/{sample}_analysis.csv"] = NOON
        files[f"{EXPERIMENT}/raw/{sample}_raw.tif"] = NOON + HOUR
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    raised = _raised(project, CONFLICT)
    assert [name for name, _ in raised] == ["vorn_001_raw.tif", "vorn_002_raw.tif"]
    assert len({value for _, value in raised}) == 1


def test_two_clocks_of_different_kinds_or_a_copy_s_order_no_pair() -> None:
    """A content clock is never read against a filesystem one.

    A file born after it was changed (a copy) orders nothing. Two filesystem clocks, neither of
    them a copy's, order a pair, and two content clocks do where the filesystem gives none."""
    from ingence.core.outcomes import TimeSource
    from ingence.rulebook.predicates.propose import _order

    parameters = {"tolerance_seconds": 60}

    def end(entry_id: str, **clocks: object) -> EntryRecords:
        return EntryRecords(entry=_entry(entry_id).model_copy(update=clocks))

    def pair(source: EntryRecords, target: EntryRecords) -> PairRecords:
        return PairRecords(
            source=source, target=target, within="w", source_membership="a", target_membership="b"
        )

    second = 1_000_000_000
    early, late = NOON * second, (NOON + HOUR) * second
    filed = end("a", modified_ns=early)
    later = end("b", modified_ns=late)
    assert _order(pair(filed, later), parameters).direction == 1
    copied = end(
        "c",
        modified_ns=late,
        created_ns=late + HOUR * second,
        created_time_source=TimeSource.BIRTHTIME,
    )
    assert _order(pair(filed, copied), parameters).direction == 0
    dived = end(
        "d",
        modified_ns=late,
        event_time_ns=early - HOUR * second,
        event_time_source=TimeSource.INSTRUMENT,
    )
    # Two filesystem clocks, neither a copy's, order the pair; a content clock on one end never
    # takes that order away (a dive agreeing would otherwise break a link).
    assert _order(pair(filed, dived), parameters).direction == 1
    other = end(
        "e", modified_ns=early, event_time_ns=early, event_time_source=TimeSource.INSTRUMENT
    )
    assert _order(pair(other, dived), parameters).direction == 1
    # Where the two kinds disagree, the content clocks add a doubt said on the pair.
    from ingence.rulebook.predicates.propose import _clock_doubts

    assert [part.name for part in _clock_doubts(pair(other, dived), parameters)] == [
        "clocks_disagree"
    ]
    # With no filesystem clock on either end, two content clocks order the pair.
    content_only = end("f", event_time_ns=early, event_time_source=TimeSource.INSTRUMENT)
    content_later = end("g", event_time_ns=late, event_time_source=TimeSource.INSTRUMENT)
    assert _order(pair(content_only, content_later), parameters).direction == 1


def test_on_a_related_pair_every_word_but_unrelated_confirms_and_says_what_it_says() -> None:
    """An undirected pair's two ends sort by identifier.

    So "made from it" and "the other way round" each say one was made from the other. Both
    confirm the related proposal, as "related" does, and "unrelated" rejects it. On a proposed
    derivation, "reversed" and "related" reject it."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.verdicts import kind_for

    on_related = {word: kind_for(word, primitive="concerns") for word in CANDIDATE_WORDS}
    assert on_related == {
        "derived": RecordEventKind.CONFIRM,
        "reversed": RecordEventKind.CONFIRM,
        "related": RecordEventKind.CONFIRM,
        "unrelated": RecordEventKind.REJECT,
        "cannot_say": RecordEventKind.ANNOTATE,
    }
    assert kind_for("reversed", primitive="derives") is RecordEventKind.REJECT
    assert kind_for("related", primitive="derives") is RecordEventKind.REJECT


def test_clocks_are_read_from_the_pass_s_own_views_of_an_entry() -> None:
    """A rule reads clocks from read-side views of its entries as well as from records.

    The pass hands a rule read-side views, not records. The test's movie carries an instrument
    clock, and the clocks are read from either."""
    from ingence.core.outcomes import TimeSource
    from ingence.rulebook.predicates.classify import credible_clocks
    from ingence.storage.views import view_of

    second = 1_000_000_000
    entry = _entry("kelt_009.nd2", NOON + HOUR).model_copy(
        update={"event_time_ns": NOON * second, "event_time_source": TimeSource.INSTRUMENT}
    )
    expected = ((NOON * second, "instrument_clock"), ((NOON + HOUR) * second, "modification"))
    assert credible_clocks(entry) == expected
    assert credible_clocks(view_of(entry)) == expected


def test_a_name_holding_an_acquisition_s_full_file_name_is_made_from_that_one_alone(
    tmp_path,
) -> None:
    """A derived name holding an acquisition's whole file name names that acquisition alone.

    The whole file name includes its format's extension. The name names no shorter acquisition
    inside it. Where that acquisition is not in the file's experiment, it is named for none."""
    experiment = "2020-03-09 zeph kelt"
    files = {
        f"{experiment}/raw/zephkelt.nd2": NOON,
        f"{experiment}/raw/zephkelt 120min-1.nd2": NOON,
        f"{experiment}/crops/Traj_zephkelt 120min-1.nd2_crop.tif.xml": NOON + HOUR,
        f"{experiment}/crops/Traj_zephkelt 240min-2.nd2_crop.tif.xml": NOON + HOUR,
    }
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    assert _derived_in(project, NAMED_FOR) == {
        (NAMED_FOR, "zephkelt 120min-1.nd2", "Traj_zephkelt 120min-1.nd2_crop.tif.xml")
    }


def test_a_whole_file_name_is_found_as_the_stem_path_finds_a_name(tmp_path) -> None:
    """Three files each have a right source that the whole-file-name rule must find.

    Replicate days holding one movie's name: the nearest by folder, as a stem is matched.
    A tool writing a name's spaces as underscores: spaces, underscores and hyphens alike.
    A ``.nd2`` in another experiment: the extensions read are the file's own experiment's, so a
    projection of a ``.czi`` movie is named for it."""
    files = {
        "2020-03-10 kelt vorn/day1/raw/kelt_013.nd2": NOON,
        "2020-03-10 kelt vorn/day2/raw/kelt_013.nd2": NOON,
        "2020-03-10 kelt vorn/day1/analysis/Traj_kelt_013.nd2_crop.csv": NOON + HOUR,
        "2020-03-11 zeph kelt/raw/zeph kelt 120min-1.nd2": NOON,
        "2020-03-11 zeph kelt/analysis/Traj_zeph_kelt_120min-1.nd2_crop.csv": NOON + HOUR,
        "2020-03-12 brisa kelt/raw/kelt_014.czi": NOON,
        "2020-03-12 brisa kelt/analysis/MAX_kelt_014.nd2 - C=0.csv": NOON + HOUR,
    }
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT s.relative_path, t.name FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id JOIN entries t ON t.entry_id = c.target_id "
            "WHERE c.rule_id = ?",
            [NAMED_FOR],
        ).fetchall()
    found = {str(target): str(source).replace("\\", "/") for source, target in rows}
    assert found["Traj_kelt_013.nd2_crop.csv"] == "2020-03-10 kelt vorn/day1/raw/kelt_013.nd2"
    assert found["Traj_zeph_kelt_120min-1.nd2_crop.csv"].endswith("zeph kelt 120min-1.nd2")
    assert found["MAX_kelt_014.nd2 - C=0.csv"].endswith("kelt_014.czi")


def test_a_name_holding_another_file_s_whole_name_is_made_from_that_file(tmp_path) -> None:
    """A movie's whole file name glued to the next word still names it.

    A track file whose name holds a crop's whole file name is made from the crop, the longest such
    name winning, whether or not the crop is an acquisition, and the crop keeps its own link to the
    movie. A name embedding a short unmatched file name before a longer acquisition's name keeps the
    acquisition its stem names."""
    files = {
        "2020-03-13 kelt vorn/raw/kelt_007_B.nd2": NOON,
        "2020-03-13 kelt vorn/masks/Traj_kelt_007_B.nd2zeph_mask.tif": NOON + HOUR,
        "2020-03-14 kelt vorn/raw/kelt_007.nd2": NOON,
        "2020-03-14 kelt vorn/crops/kelt_007-1.tif": NOON + HOUR,
        "2020-03-14 kelt vorn/tracks/Traj_kelt_007-1.tif_tracks.tif": NOON + 2 * HOUR,
        "2020-03-15 brisa vorn/raw/brisakelt_long_name.nd2": NOON,
        "2020-03-15 brisa vorn/analysis/a.nd2_brisakelt_long_name_tracks.csv": NOON + HOUR,
    }
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    found = {target: source for _, source, target in _derived_in(project, NAMED_FOR)}
    assert found["Traj_kelt_007_B.nd2zeph_mask.tif"] == "kelt_007_B.nd2"
    assert found["kelt_007-1.tif"] == "kelt_007.nd2"
    assert found["Traj_kelt_007-1.tif_tracks.tif"] == "kelt_007-1.tif"
    assert found["a.nd2_brisakelt_long_name_tracks.csv"] == "brisakelt_long_name.nd2"


def test_a_whole_name_inside_a_name_never_takes_the_movie_its_stem_names(tmp_path) -> None:
    """A file's whole name ending the name a file truly holds, by chance, never makes it the source
    when it is another movie's output: the overlay keeps the longer movie its stem names. A common
    short name (``1.tif``, ``a.tif``) elsewhere in the experiment, named for no movie, never takes a
    file's movie away. A whole name is read only from the start of a name part:
    ``xkelt_026_mask.tif`` holds no ``kelt_026_mask.tif``."""
    files = {
        "2020-04-01 kelt zeph/raw/kelt_026.nd2": NOON,
        "2020-04-01 kelt zeph/raw/zeph_kelt_026.nd2": NOON,
        "2020-04-01 kelt zeph/analysis/kelt_026_mask.tif": NOON + HOUR,
        "2020-04-01 kelt zeph/analysis/zeph_kelt_026_mask.tif_overlay.csv": NOON + 2 * HOUR,
        "2020-04-01 kelt zeph/analysis/xkelt_026_mask.tif_overlay.csv": NOON + 2 * HOUR,
        "2020-04-02 kelt vorn/raw/kelt_022.nd2": NOON,
        "2020-04-02 kelt vorn/scratch/1.tif": NOON + HOUR,
        "2020-04-02 kelt vorn/analysis/kelt_022_1.tif_roi.csv": NOON + 2 * HOUR,
        "2020-04-03 kelt vorn/raw/kelt_023.nd2": NOON,
        "2020-04-03 kelt vorn/notes/a.tif": NOON + HOUR,
        "2020-04-03 kelt vorn/analysis/kelt_023_a.tif_profile.csv": NOON + 2 * HOUR,
    }
    pool = tmp_path / "pool"
    _write(pool, dict.fromkeys(files, "x"), files)
    project = _project(tmp_path, pool)

    _infer(project, tmp_path)

    found: dict[str, set[str]] = {}
    for _, source, target in _derived_in(project, NAMED_FOR):
        found.setdefault(target, set()).add(source)
    assert found["kelt_026_mask.tif"] == {"kelt_026.nd2"}
    assert found["zeph_kelt_026_mask.tif_overlay.csv"] == {"zeph_kelt_026.nd2"}
    assert "xkelt_026_mask.tif_overlay.csv" not in found
    assert found["kelt_022_1.tif_roi.csv"] == {"kelt_022.nd2"}
    assert found["kelt_023_a.tif_profile.csv"] == {"kelt_023.nd2"}
    assert "1.tif" not in found
    assert "a.tif" not in found
