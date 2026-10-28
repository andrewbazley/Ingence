"""The Review queue: what it groups, what it ranks on, and what it refuses to store.

Three properties matter more than any particular ordering, because a queue is only worth having if
a person can finish it and trust what put things where:

A question is the unit, not a finding. One uncertain term raised on four files is one question
asked four times, which is what turns the findings of a large tree into a page.

The ranking stores nothing. ``Finding`` gains no field, and a request that reorders the queue
leaves no trace, which is what makes retuning it free.

Nothing is summed into a score. The dimensions are counts and a tier, each shown beside the
question it ranked, so a person can disagree with the order and say why.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from fixtures.domain_skew import ask_every_tie
from fixtures.revision_4 import write_revision
from ingence.api.routes.review import (
    DEFAULT_ORDER,
    DIMENSIONS,
    Dimension,
    UnknownDimensionError,
    review,
)
from ingence.core.project import Project
from ingence.core.schema import Finding
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"

#: ``sim`` is a declared tie between a simulation and structured illumination, two packs'
#: techniques, and ``inventory`` between an inventory file and a manifest. Both are shipped
#: ambiguities, so the pool needs no invented vocabulary to raise a real question. Every stem
#: beside them is invented and binds to nothing.
TIED_ON_THREE = ("vorn_sim_raw.csv", "vorn_sim_processed.csv", "vorn_sim_analysis.csv")
TIED_ON_ONE = "brisa_inventory.txt"

#: A folder somebody repudiated, which ``repudiated_by_name`` raises at folder level. Its
#: question is the one the Dictionary offers no ready-made answer for: the doubt term
#: resolved cleanly, so there are no competing senses to pick between, and what to do about
#: the folder is entirely a person's to say.
REPUDIATED_FOLDER = "kelt_deprecated"


def build(base: Path, *, with_revision_4: bool) -> Project:
    """A pool raising three questions of deliberately different shapes.

    One tied term on three files, one tied term on one file, and one repudiated folder that
    offers no senses at all. Under revision 4 the three files sharing a stem in one folder
    are claimed into a name identity class, which is what gives ``classes_touched`` something
    real to count. Without it no grouping rule publishes, because none names a defeater.
    """
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    for name in TIED_ON_THREE:
        (pool / "kelt" / name).write_text("a,b\n1,2\n")
    (pool / "kelt" / TIED_ON_ONE).write_text("notes")
    (pool / REPUDIATED_FOLDER).mkdir(parents=True)
    (pool / REPUDIATED_FOLDER / "trevik.txt").write_text("x")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    if with_revision_4:
        write_revision(project.path)
    project = ask_every_tie(project.path)  # a tied word in a few names is still asked
    run_census(project, run_id=CENSUS, started_at="t")
    # Without revision 4 the book is at revision 3 (every project's rules hold it), so the queue
    # holds the three questions the pool was written to raise.
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook

    load_builtins()
    first_four = None if with_revision_4 else effective_rulebook(load_revisions(), at=3)
    run_inference(project, run_id=INFERENCE, started_at="t", rulebook=first_four)
    return project


@pytest.fixture(scope="module")
def shipped(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return build(tmp_path_factory.mktemp("review-shipped"), with_revision_4=False)


@pytest.fixture(scope="module")
def grouped(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return build(tmp_path_factory.mktemp("review-grouped"), with_revision_4=True)


def queue(project: Project, **arguments: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return review(catalog, **arguments)


def question_for(page: dict[str, Any], raised: str) -> dict[str, Any]:
    matched = [item for item in page["questions"] if item["raised"] == raised]
    assert matched, f"no question for {raised!r} in {[i['raised'] for i in page['questions']]}"
    return matched[0]


# ---------------------------------------------------------------------------
# A question is the unit.
# ---------------------------------------------------------------------------


def test_one_term_raised_on_many_files_is_one_question(shipped: Project) -> None:
    """The collapse that makes the queue finishable.

    On real folders nearly every finding raised comes from one rule, so grouping by rule ranks
    nothing. Grouping by what was raised makes a page a person can finish, with one question for
    each uncertain term.
    """
    page = queue(shipped)
    assert page["totals"]["findings"] > page["totals"]["questions"]
    assert {item["raised"] for item in page["questions"]} == {"sim", "inventory", "deprecated"}
    assert question_for(page, "sim")["findings"] == len(TIED_ON_THREE)


def test_a_question_carries_every_finding_it_covers(shipped: Project) -> None:
    """Grouping must hide nothing, so a person can still walk to any one file."""
    page = queue(shipped)
    covered = sum(int(item["findings"]) for item in page["questions"])
    with open_catalog(shipped.path) as catalog:
        assert covered == catalog.count("findings")


def test_the_counts_are_exact_and_the_samples_are_bounded(shipped: Project) -> None:
    """A question covering a great many files must not put every one of their ids on a page."""
    page = queue(shipped, sample=2)
    asked = question_for(page, "sim")
    assert asked["entries_affected"] == len(TIED_ON_THREE)
    assert len(asked["subjects"]) == 2
    assert len(asked["finding_ids"]) == 2


# ---------------------------------------------------------------------------
# A question shows the file it is about.
# ---------------------------------------------------------------------------

#: 2020-01-01 00:00:00 UTC, before the date revision 4's stub rule names.
LONG_AGO = 1_577_836_800


def test_a_question_about_one_file_shows_its_path_size_and_date(tmp_path: Path) -> None:
    """A file out of reach is judged by its folder, its size and when it was last written.

    Without the join the page would ask a person to judge a digest. The identifier stays
    beside the path, because it is what the Record and every other view cite. A folder scope's
    subject is ``folder:<entry id>``, and it is shown by the folder's path in the same way.

    No rule in force raises an entry identifier as its value. ``content_unreadable`` raises the
    way the content is out of reach, so one question covers every file out of reach the same way.
    The join on a raised value is kept for a rule that raises one and is exercised by the term
    question in the test below, where a raised value joins to nothing and is shown as it is.
    """
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    stub = pool / "kelt" / "vorn.dat"
    stub.write_bytes(b"")
    os.utime(stub, (LONG_AGO, LONG_AGO))
    for part in ("bin", "lib"):
        (pool / "brisatool" / part).mkdir(parents=True)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    project = ask_every_tie(project.path)  # a tied word in a few names is still asked
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFERENCE, started_at="t")

    page = queue(project)
    (asked,) = [item for item in page["questions"] if item["rule_id"] == "content_unreadable"]
    assert asked["raised"] == "empty_and_old", "the way it is out of reach"
    assert asked["raised_file"] is None, "which is no file, so it joins to nothing"
    (shown,) = asked["subject_files"]
    assert shown["id"] == asked["subjects"][0]
    assert Path(shown["path"]).resolve() == stub.resolve()
    assert shown["size_bytes"] == 0
    assert shown["modified"] == "2020-01-01 00:00:00 UTC"

    (layout,) = [
        item for item in page["questions"] if item["rule_id"] == "installed_software_in_folder"
    ]
    (subject,) = layout["subjects"]
    assert subject.startswith("folder:")
    (folder,) = layout["subject_files"]
    assert folder["id"] == subject
    assert Path(folder["path"]).resolve() == (pool / "brisatool").resolve()


def test_sampled_subjects_are_listed_by_path_with_the_identifier_beside(shipped: Project) -> None:
    """A term is not a file, so a term question's raised value joins to nothing and is shown as
    it was, and its subjects -- the files that raised it -- are each shown by path."""
    page = queue(shipped)
    term = question_for(page, "sim")
    assert term["raised_file"] is None
    assert [shown["id"] for shown in term["subject_files"]] == term["subjects"]
    assert sorted(Path(shown["path"]).name for shown in term["subject_files"]) == sorted(
        TIED_ON_THREE
    )


# ---------------------------------------------------------------------------
# What it ranks on.
# ---------------------------------------------------------------------------


def test_the_queue_leads_with_the_term_more_files_ask_about(shipped: Project) -> None:
    """And the two questions on one file each are separated by how cheap the answer is.

    ``inventory`` offers two senses a click settles. The repudiated folder offers none, so it
    sorts behind under a dimension that otherwise runs smallest-first. That is the default
    order's last two dimensions doing visible work rather than being decoration.
    """
    page = queue(shipped)
    assert [item["raised"] for item in page["questions"]] == ["sim", "inventory", "deprecated"]


def test_every_declared_dimension_is_computed_on_every_question(shipped: Project) -> None:
    """A dimension declared and never filled would rank nothing while appearing to.

    Asked of the declarations rather than of a list written here, so a dimension added later
    is covered by this test on the day it is added.
    """
    page = queue(shipped)
    assert [entry["name"] for entry in page["dimensions"]] == list(DEFAULT_ORDER)
    for item in page["questions"]:
        for dimension in DIMENSIONS:
            assert dimension.name in item, f"{dimension.name} is declared and not computed"
            assert isinstance(item[dimension.name], int)


def test_every_dimension_declares_which_way_round_it_runs() -> None:
    """Two of them run opposite ways, and a reader guessing wrong reads the queue backwards."""
    assert all(isinstance(dimension, Dimension) for dimension in DIMENSIONS)
    assert {dimension.direction for dimension in DIMENSIONS} <= {"more", "fewer"}
    assert any(dimension.direction == "fewer" for dimension in DIMENSIONS)


def test_conclusions_blocked_counts_the_rules_that_could_not_decide(shipped: Project) -> None:
    """A rule that could not decide is exactly a conclusion an answer would unblock.

    Counted from interpretation failures alone: an access or comprehension failure is a mount
    or a missing extractor, and no amount of a person's attention to a term fixes either.
    """
    asked = question_for(queue(shipped), "sim")
    with open_catalog(shipped.path) as catalog:
        failures = catalog.query(
            "SELECT count(*) FROM rule_failures WHERE failure_class = 'interpretation'"
        ).fetchone()
    assert asked["conclusions_blocked"] > 0
    assert asked["conclusions_blocked"] <= int(failures[0])
    assert asked["rules_blocked"] >= 1


def test_a_term_inside_a_class_outranks_one_in_a_file_nothing_depends_on(
    grouped: Project,
) -> None:
    """``classes_touched`` needs a Rulebook whose grouping rules publish.

    On the shipped Rulebook no grouping rule names a defeater, so none publishes and this
    dimension is zero everywhere -- correctly, and uninformatively. Under revision 4 the
    three files sharing a stem are claimed into a name identity, and the dimension separates
    them from the file that is in no class.
    """
    page = queue(grouped)
    assert question_for(page, "sim")["classes_touched"] > 0
    assert question_for(page, "inventory")["classes_touched"] == 0


def test_a_recommended_default_is_withheld_for_a_tie_and_says_why(shipped: Project) -> None:
    """Offering the first sense would be arbitration by list order, not a conclusion.

    A tie means nothing about the token separates the senses, which is why the rule raised it
    rather than resolving it. The queue says that in words instead of leaving a blank a page
    would render as "no suggestion available".
    """
    asked = question_for(queue(shipped), "sim")
    assert asked["recommended"]["value"] is None
    assert "stand equal" in asked["recommended"]["because"]
    assert asked["tier"] == "defeasible"
    assert asked["default_standing"] > 0


def test_the_senses_are_named_rather_than_identified(shipped: Project) -> None:
    """The queue names each sense, though an alternative carries only its identifier.

    A queue that asked "does this mean term_3a42a719 or term_519b98a9" would be unusable, and
    it is only readable because every inference carries the run's own vocabulary forward.
    """
    asked = question_for(queue(shipped), "sim")
    terms = {sense["term"] for sense in asked["senses"]}
    assert terms == {"structured_illumination", "simulation"}
    assert not any(str(term).startswith("term_") for term in terms)
    assert asked["senses_offered"] == 2


def test_a_question_offering_no_ready_made_sense_sorts_last(shipped: Project) -> None:
    """Zero is the most expensive answer there is, so it must not sort as the cheapest.

    ``senses_offered`` runs smallest-first because two meanings is one click, and a question
    with none is either a term nobody has defined or a question of another shape. Ranked on
    that dimension alone, the repudiated folder's question comes last although zero is the
    smallest number on the page: the count reported is the fact, and only the ordering
    treats zero as large.
    """
    page = queue(shipped, order=["senses_offered"])
    ranked = [(item["raised"], item["senses_offered"]) for item in page["questions"]]
    assert ranked[-1] == ("deprecated", 0)
    assert all(count >= 1 for _, count in ranked[:-1])
    asked = question_for(page, "deprecated")
    assert asked["senses"] == []
    assert asked["recommended"]["value"] is None
    assert "no ready-made answer" in asked["recommended"]["because"]


# ---------------------------------------------------------------------------
# Tunable, and a rendering rather than an interpretation.
# ---------------------------------------------------------------------------


def test_the_order_is_tunable_and_changes_nothing_but_the_order(shipped: Project) -> None:
    """Reordering a queue is not an interpretation and must not become one.

    The same questions with the same numbers, in a different sequence, with no re-run and
    nothing written. That is the property that lets the ranking be retuned freely.
    """
    default = queue(shipped)
    reordered = queue(shipped, order=["senses_offered", "entries_affected"])
    assert reordered["order"] == ["senses_offered", "entries_affected"]
    assert default["totals"] == reordered["totals"]
    by_raised = {item["raised"]: item for item in default["questions"]}
    for item in reordered["questions"]:
        twin = by_raised[item["raised"]]
        for dimension in DIMENSIONS:
            assert item[dimension.name] == twin[dimension.name]


def test_a_dimension_the_queue_does_not_compute_is_refused(shipped: Project) -> None:
    """Refused rather than dropped, on the Rulebook loader's precedent.

    A queue ordered differently from the way the page says it is ordered gives a reader no
    way to tell, which is the failure the loader refuses a silently dropped rule for.
    """
    with pytest.raises(UnknownDimensionError, match="no dimension named 'urgency'"):
        queue(shipped, order=["urgency"])
    with pytest.raises(UnknownDimensionError, match="at least one dimension"):
        queue(shipped, order=[])


def test_the_ranking_is_stored_nowhere(shipped: Project) -> None:
    """Ranking writes nothing: the project is byte-identical afterwards.

    A digest over every file in the project directory, before and against after. A ranking
    that wrote anything -- a cache, a column, a decision record -- would show up here, and
    the reason it must not is that a stored ranking can drift from the evidence that produced
    it while still looking authoritative.
    """
    before = _digest_tree(shipped.path)
    queue(shipped)
    queue(shipped, order=["senses_offered"])
    assert _digest_tree(shipped.path) == before


def test_the_finding_record_gains_nothing(shipped: Project) -> None:
    """Salience is computed in the view and is not a field of the finding.

    A field, or a record that cites a finding, would be another shape of the model. The assertion
    on the model makes adding one a deliberate decision rather than a passing edit.
    """
    assert "salience" not in Finding.model_fields
    with open_catalog(shipped.path) as catalog:
        columns = {
            row[0]
            for row in catalog.query("SELECT column_name FROM (DESCRIBE findings)").fetchall()
        }
    assert not any("salience" in column for column in columns)


def _digest_tree(root: Path) -> str:
    digest = hashlib.blake2b()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# A project with nothing to review.
# ---------------------------------------------------------------------------


def test_a_project_with_no_run_answers_with_an_empty_queue(tmp_path: Path) -> None:
    """The landing case, not a broken one: the Dashboard opens before anything is inferred."""
    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    page = queue(project)
    assert page["questions"] == []
    assert page["totals"] == {"questions": 0, "findings": 0, "set_aside": 0}
    assert page["set_aside"] == []
    assert [entry["name"] for entry in page["dimensions"]] == list(DEFAULT_ORDER)
