"""Revision 4 composes after the shipped revisions and does what the generated pool expects.

The declarations are measured before anything ships. What is held here is that they load, compose
and supersede exactly what they say, and that on the pool written for them the first proposing
rule proposes exactly the derivations written down before it first ran.

A generated pool shows that a rule does what its sentence says, never that it is right for a
laboratory. Where revision 4 and an expectation disagree by a decision, the disagreement is named
here beside the decision, and the pool is not changed to hide it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from fixtures.laboratory_pool import (
    Concludes,
    Groups,
    Proposes,
    Retracts,
    expectations_over,
    write_pool,
)
from fixtures.one_folder_pairs import allow_pairs_in_one_folder
from fixtures.revision_4 import REVISION, write_revision
from ingence.core.project import Project
from ingence.infer.claims import why_not_claim_ready
from ingence.infer.run import run_inference
from ingence.rulebook import project_rulebook, shipped_rulebook
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

#: The rule an expectation names, as revision 4 declares it. A project revision renames what it
#: changes, and the pool's expectations name rules as the shipped Rulebook does.
IN_REVISION_4 = {
    "stage_from_name": "pipeline_stage_from_name",
    "file_role_from_name": "file_role_unless_program",
    "same_name_family": "name_family_without_stages",
    "filesystem_noise_from_name": "filesystem_noise_from_spelling",
}

#: The first proposing rule, and the index it draws within.
PROPOSING = "derivation_within_name_identity"


def _superseded_by_revision_5() -> frozenset[str]:
    from ingence.rulebook.revision_5 import REVISION as REVISION_5

    changes = REVISION_5["changes"]
    assert isinstance(changes, list)
    return frozenset(str(change["supersede"]) for change in changes if "supersede" in change)


#: The rules of revision 4 that revision 5 supersedes.
_LATER = _superseded_by_revision_5()
IDENTITY = "same_name_identity"
#: The rule draws within the identity compared across an experiment's subfolders, which is the
#: identity within one folder for a file beneath no experiment. The pool's expectations name the
#: index as they were written, and no folder of the pool splits one chain across stage
#: subfolders, so the two draw the same pairs here.
DRAWN_WITHIN = "same_name_identity_within_experiment"
#: Where both ends are named for one acquisition, that group's derivation proposes the pair
#: instead. The pool's expectations name the pairs, and which of the two readings proposes each is
#: revision 4's to say, so the two are read together.
NAMED_FOR = "derivation_named_for_an_acquisition"
#: The rule proposing an identity's pairs no time orders.
RELATED = "related_within_name_identity"

#: Conditions the pool states that revision 4 does not adopt, each with the answer that left it
#: out.
NOT_ADOPTED = {
    "name_states_two_stages": "Q8: the stage rule reads pipeline stages only, in its place",
    "format_contradicts_role": "Q7: the program-format defeater, and no other",
    "emitted_name_from_siblings": "Q10: after the proofs, on a siblings shape",
}

#: The editor's lock file beside a processed table, and the raw table in its folder. Revision 4
#: leaves an office suite's lock-file prefix unread, so the noise condition the pool states
#: retracts neither its family nor its identity claim, and the raw table is proposed as its
#: source: a known false candidate.
LOCK_FILE = "saltra-trevik/~$saltra_trevik_processed.csv"
RAW_BESIDE_IT = "saltra-trevik/saltra_trevik_raw.csv"

#: The family keyed with stage words left out, as the pool's expectations spell its parameters.
_WITHOUT_STAGE_WORDS = (("ignored_theme_kinds", ("stage",)),)

#: The three replicates and the file processed from them, which are one experiment: the identity
#: leaves out stage words and replicate components, so all four share it and the proposing rule
#: draws the three raw-to-processed pairs within it.
#:
#: The pool's expectations predate that and are not edited to match. They were written when an
#: index kept two names apart whatever the Dictionary made of it, and they say the replicate case
#: proposes nothing within the identity. Ignoring replicates is what the identity is meant to do,
#: so these three are correct and the pool is left as it is: a corpus iterated against until the
#: output looks right has stopped measuring anything.
_REPLICATES = tuple(f"mirdo-saltra/mirdo_saltra_raw_rep{number}.tif" for number in (1, 2, 3))
_PROCESSED_FROM_THEM = "mirdo-saltra/mirdo_saltra_processed.tif"
WITHIN_ONE_EXPERIMENT = {
    (replicate, _PROCESSED_FROM_THEM, "circumstantial") for replicate in _REPLICATES
}


@pytest.fixture(scope="module")
def measured(tmp_path_factory: pytest.TempPathFactory) -> tuple[Project, tuple[str, ...]]:
    """The generated pool, indexed and inferred once under revision 4.

    Its files are written in the order its expectations give. A derivation's direction is the
    earlier file's, so each expected source is written an hour before its target, and the pool's
    pairs, each in one folder, are let be ordered there.
    """
    base = tmp_path_factory.mktemp("revision-4")
    pool = write_pool(base / "pool")
    _written_in_order(pool.root, pool.written)
    project = Project.create(base / "project", created_at="t", roots=[pool.root])
    write_revision(project.path)
    allow_pairs_in_one_folder(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    report = run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    assert not report.cancelled
    return project, pool.written


def _written_in_order(root: Path, written: tuple[str, ...]) -> None:
    """Each expected source an hour before its target, every other file at the first hour."""
    import os

    pairs = [
        (expectation.source, expectation.target)
        for expectation in _expected(written)
        if isinstance(expectation, Proposes)
    ]
    pairs += [(RAW_BESIDE_IT, LOCK_FILE)]
    pairs += [(source, target) for source, target, _ in WITHIN_ONE_EXPERIMENT]
    turn = dict.fromkeys(written, 0)
    for _ in range(len(pairs)):
        for source, target in pairs:
            if source in turn and target in turn and turn[target] <= turn[source]:
                turn[target] = turn[source] + 1
    for path, at in turn.items():
        moment = 1_583_064_000 + at * 3600
        file = root.joinpath(*path.split("/"))
        if file.is_file() and not file.is_symlink():
            os.utime(file, (moment, moment))


def _rows(project: Project, sql: str, *parameters: object) -> list[tuple[object, ...]]:
    with open_catalog(project.path) as catalog:
        return catalog.query(sql, list(parameters)).fetchall()


def _path(value: object) -> str:
    return str(value).replace("\\", "/")


def _in_revision_4(rule: str) -> str:
    return IN_REVISION_4.get(rule, rule)


def _expected(written: tuple[str, ...]) -> tuple[object, ...]:
    """The pool's expectations over what was written, less the conditions revision 4 leaves out."""
    return tuple(
        expectation
        for expectation in expectations_over(written)
        if not (isinstance(expectation, Retracts) and expectation.condition in NOT_ADOPTED)
    )


def test_revision_4_composes_after_the_shipped_revisions_and_supersedes_what_it_says(
    tmp_path: Path,
) -> None:
    """Revision 4 ships in every project's Rulebook, after the book at revision 3."""
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.resolve import effective_rulebook

    write_revision(tmp_path)
    book = project_rulebook(tmp_path)
    #: Rules revision 4 declares and ships switched off: declared, never in force until a project
    #: enables them (``enable:`` in its rulebook folder).
    shipped_off = {str(rule["id"]) for rule in REVISION["rules"] if rule.get("enabled") is False}
    # Revision 5 supersedes some of revision 4's rules in turn.
    declared = {str(rule["id"]) for rule in REVISION["rules"]} - shipped_off - _LATER
    superseded = {
        str(change["supersede"]) for change in REVISION["changes"] if "supersede" in change
    }
    before = effective_rulebook(load_revisions(), at=3)

    assert all(book.by_id(rule_id) is not None for rule_id in declared)
    assert all(book.by_id(rule_id) is None for rule_id in superseded)
    assert all(before.by_id(rule_id) is not None for rule_id in superseded)
    inactive = dict(book.inactive)
    assert all("superseded" in inactive[rule_id] for rule_id in superseded)
    assert all(dict(book.inactive)[rule_id] == "disabled" for rule_id in shipped_off)
    assert book.digest == shipped_rulebook().digest
    assert book.digest != before.digest


def test_every_rule_naming_a_defeater_may_publish_and_the_index_is_in_force(tmp_path: Path) -> None:
    """A defeater that is not in force would leave its rule counted and silent."""
    write_revision(tmp_path)
    book = project_rulebook(tmp_path)
    in_force = {rule.rule_id for rule in book.rules}
    for declared in REVISION["rules"]:
        if declared.get("enabled") is False or str(declared["id"]) in _LATER:
            continue
        rule = book.by_id(str(declared["id"]))
        assert rule is not None
        if rule.defeated_by:
            assert why_not_claim_ready(rule, in_force) is None, rule.rule_id
    proposing = book.by_id(PROPOSING)
    assert proposing is not None
    assert proposing.candidates_within == DRAWN_WITHIN
    assert DRAWN_WITHIN in in_force
    assert IDENTITY in in_force


def test_the_proposing_rules_propose_exactly_what_was_written_before_they_ran(
    measured: tuple[Project, tuple[str, ...]],
) -> None:
    """The derivations are the ones the pool's expectations name, in their directions, by the time
    and never by the stage words. The pool's files are written in the order its expectations
    give, so each expected source is the earlier file. A derivation is proposed within the
    identity, or from an acquisition where the file is named for one."""
    project, written = measured
    rows = _rows(
        project,
        "SELECT s.relative_path, t.relative_path, c.decision_tier, c.defeaters_json "
        "FROM candidates c JOIN entries s ON s.entry_id = c.source_id "
        "JOIN entries t ON t.entry_id = c.target_id WHERE c.rule_id IN (?, ?)",
        PROPOSING,
        NAMED_FOR,
    )
    # One pair, one candidate: the two readings never propose the same pair twice.
    assert len(rows) == len({(source, target) for source, target, _, _ in rows})
    proposed = {
        (_path(source), _path(target)) for source, target, tier, _ in rows if tier != "blocked"
    }
    # A program written before the image of its identity is proposed and refused, never dropped:
    # it is neither what a step read nor what it made (the endpoint rule, version 2).
    for source, target, tier, defeaters in rows:
        if tier == "blocked":
            fired = {item["rule_id"] for item in json.loads(str(defeaters)) if item["fired"]}
            assert fired == {"derivation_endpoints_hold_roles_or_a_scripts_calls"}, (source, target)
    expected = {
        (expectation.source, expectation.target)
        for expectation in _expected(written)
        if isinstance(expectation, Proposes) and expectation.within == IDENTITY
    }
    assert proposed == (
        expected | {(RAW_BESIDE_IT, LOCK_FILE)} | {(s, t) for s, t, _ in WITHIN_ONE_EXPERIMENT}
    )


@pytest.mark.parametrize(
    "rule", ["stage_from_name", "file_role_from_name", "acquisition_stage_from_format"]
)
def test_a_classifying_rule_concludes_exactly_what_the_pool_says(
    measured: tuple[Project, tuple[str, ...]], rule: str
) -> None:
    project, written = measured
    rows = _rows(
        project,
        "SELECT e.relative_path, c.object_reference FROM claims c "
        "JOIN entries e ON e.entry_id = c.subject WHERE c.rule_id = ?",
        _in_revision_4(rule),
    )
    concluded = {(_path(path), str(value)) for path, value in rows}
    expected = {
        (expectation.member, sense)
        for expectation in _expected(written)
        if isinstance(expectation, Concludes) and expectation.rule == rule
        for sense in expectation.senses
    }

    assert concluded == expected


def test_each_defeat_condition_retracts_exactly_what_the_pool_says_but_for_the_lock_file(
    measured: tuple[Project, tuple[str, ...]],
) -> None:
    project, written = measured
    named = {
        _in_revision_4(expectation.rule)
        for expectation in _expected(written)
        if isinstance(expectation, Retracts)
    }
    rows = _rows(
        project,
        "SELECT c.rule_id, e.relative_path, c.defeaters_json FROM claims c "
        "JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.resolution_status = 'examined_conflicting'",
    )
    retracted = {
        (str(defeater["rule_id"]), str(rule_id), _path(path))
        for rule_id, path, defeaters in rows
        if rule_id in named
        for defeater in json.loads(str(defeaters))
        if defeater["fired"]
    }
    expected = {
        (
            _in_revision_4(expectation.condition),
            _in_revision_4(expectation.rule),
            expectation.member,
        )
        for expectation in _expected(written)
        if isinstance(expectation, Retracts) and expectation.member != LOCK_FILE
    }

    assert retracted == expected


def test_the_family_without_stage_words_forms_exactly_the_classes_the_pool_says(
    measured: tuple[Project, tuple[str, ...]],
) -> None:
    project, written = measured
    rows = _rows(
        project,
        "SELECT c.object_reference, e.relative_path FROM claims c "
        "JOIN entries e ON e.entry_id = c.subject WHERE c.rule_id = ?",
        _in_revision_4("same_name_family"),
    )
    classes: dict[str, set[str]] = {}
    for key, path in rows:
        classes.setdefault(str(key), set()).add(_path(path))
    expected = {
        expectation.members
        for expectation in _expected(written)
        if isinstance(expectation, Groups)
        and expectation.rule == "same_name_family"
        and expectation.parameters == _WITHOUT_STAGE_WORDS
    }

    assert {frozenset(members) for members in classes.values()} == expected


def _indexed(tmp_path: Path, names: dict[str, bytes]) -> Project:
    """A tiny tree of invented names, indexed and inferred under revision 4.

    Separate from the module's generated pool on purpose: the pool's expectations are written
    before it runs and are never edited to match a result, so a case exercising one rule
    builds its own tree rather than growing the tripwire.
    """
    pool = tmp_path / "pool" / "kelt"
    pool.mkdir(parents=True)
    for name, content in names.items():
        (pool / name).write_bytes(content)
    project = Project.create(tmp_path / "project", created_at="t", roots=[tmp_path / "pool"])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project


def test_a_trajectory_is_an_analysis_and_traj_still_means_a_trajectory(tmp_path: Path) -> None:
    """``Traj_`` marks a tracking file, and that is a rule rather than a surface.

    Adding it as a second spelling of a stage would put it between two senses at equal
    precedence, and a token standing between two senses resolves to neither -- one good binding
    would become two abstentions. Concluding the stage from the sense keeps ``traj`` meaning a
    trajectory and places the file as well.
    """
    project = _indexed(
        tmp_path,
        {
            "Traj_kelt_vorn.csv": b"a,b\n1,2\n",
            "kelt_vorn.py": b"# a program named for nothing in particular\n",
            "Traj_kelt_vorn.py": b"# a tracker, not a track\n",
        },
    )
    staged = {
        _path(name): value
        for name, value in _rows(
            project,
            "SELECT e.name, c.object_reference FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = 'analysis_stage_from_derived_sense' "
            "AND c.resolution_status = 'resolved'",
        )
    }
    assert staged.get("Traj_kelt_vorn.csv") == "analysis"

    # `traj` still binds the trajectory sense rather than standing between two.
    bound = _rows(
        project,
        "SELECT b.term, b.resolution_status FROM dictionary_bindings b JOIN entries e "
        "ON e.entry_id = b.entry_id WHERE e.name = 'Traj_kelt_vorn.csv' AND b.term IS NOT NULL",
    )
    assert ("trajectory", "resolved") in [(str(term), str(status)) for term, status in bound]

    # A script named for a trajectory processes one and is not one: the program defeater retracts.
    retracted = _rows(
        project,
        "SELECT e.name FROM claims c JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.rule_id = 'analysis_stage_from_derived_sense' "
        "AND c.resolution_status <> 'resolved'",
    )
    assert _path(retracted[0][0]) == "Traj_kelt_vorn.py"


def test_a_file_whose_content_cannot_be_read_is_raised_as_the_way_it_is_out_of_reach(
    tmp_path: Path,
) -> None:
    """``content_unreadable`` replaces the stub rule.

    The old stub rule read the pair of emptiness and age as an archive link. On real laboratory
    folders, no file it raised stood for one. What is raised is the fact and which way it holds:
    an empty file is ``empty``, an empty file nothing has written to for a year is
    ``empty_and_old``, and a link whose target is gone is ``link_without_a_target``. The name is
    never read.
    """
    import os

    project = _indexed(
        tmp_path,
        {
            "kelt_vorn_raw.nd2": b"",
            "kelt_vorn_processed.nd2": b"",
            "kelt_vorn_notes.txt": b"x" * 40,
        },
    )
    root = tmp_path / "pool" / "kelt"
    old = 1_735_689_600  # 1 January 2025, before the date revision 4 names
    os.utime(root / "kelt_vorn_raw.nd2", (old, old))
    os.utime(root / "kelt_vorn_notes.txt", (old, old))
    (root / "kelt_vorn_gone.nd2").symlink_to(root / "kelt_vorn_missing.nd2")
    run_census(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    run_inference(project, run_id="run_20260101T000004Z_444444444444", started_at="t")

    raised: dict[str, set[str]] = {}
    for name, values in _rows(
        project,
        "SELECT e.name, f.raised FROM findings f JOIN entries e ON e.entry_id = f.subject "
        "WHERE f.rule_id = 'content_unreadable'",
    ):
        raised[_path(name)] = set(values)
    # The value is `empty_and_old`, not `dehydrated_or_archived`: an archive on a shared drive
    # leaves a link, not an empty file.
    assert raised.get("kelt_vorn_raw.nd2") == {"empty_and_old"}, "empty and a year old"
    assert raised.get("kelt_vorn_processed.nd2") == {"empty"}, "empty, and written just now"
    assert "kelt_vorn_notes.txt" not in raised, "old, but it holds bytes"
    # A link's own size is the length of the path it holds, so it is not empty. What it is
    # missing is its target, and the rule says that and nothing else.
    assert raised.get("kelt_vorn_gone.nd2") == {"link_without_a_target"}
