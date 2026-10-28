"""The declared workflow: its guards, and the default that has to pass them.

A user's own workflow must not create the loop that costs a file its directionality. The tests
here are the four guards that prevent it, plus the two properties that make them worth having:

The shipped default passes its own guards. A guard the default violates is a guard nobody will
trust, and it is the one test here that would have caught a plausible mistake: a default that
ends at ``merged``, a word the Dictionary declares ambiguous.

A refused workflow writes nothing. The Record is append-only, so a malformed workflow accepted
at declaration could never be taken out again, only superseded, and everything it had
re-interpreted would stay re-interpreted.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.record.log import read_events
from ingence.record.workflow import (
    DEFAULT_WORKFLOW,
    MINIMUM_RUNGS,
    LogicalContext,
    Workflow,
    WorkflowRefusedError,
    check_workflow,
    declare_workflow,
    known_stage_senses,
    logical_context_of,
    what_would_change,
    withdraw_workflow,
    workflows_in_force,
)
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

RUN = "run_20260101T000001Z_111111111111"
MOMENT = "2026-09-17T14:02:11Z"

#: A vocabulary handed in, so the guard tests say what they mean rather than depending on
#: which words the shipped Dictionary happens to hold.
STAGES = frozenset({"raw", "processed", "analysis", "pooled", "acquisition", "summary"})


@pytest.fixture
def project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn_raw.csv").write_text("a\n1\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUN, started_at="t")
    return project


# ---------------------------------------------------------------------------
# The default.
# ---------------------------------------------------------------------------


def test_the_shipped_default_passes_its_own_guards() -> None:
    """The shipped default is itself a valid workflow, and this check is not ceremonial.

    The default's rungs are ``raw``, ``processed`` and ``analysis``. A default whose last rung
    was ``merged`` would fail, because the Dictionary declares ``merged`` ambiguous between a
    merged image and merged results. A rung naming it would name a sense that resolves to
    neither. This is the check that would catch that, and every later edit of the same kind.
    """
    checked = check_workflow(
        DEFAULT_WORKFLOW.name,
        DEFAULT_WORKFLOW.rungs,
        observation=DEFAULT_WORKFLOW.observation,
    )
    assert checked == DEFAULT_WORKFLOW


def test_every_rung_of_the_default_is_a_stage_the_dictionary_knows() -> None:
    """Checked against the shipped vocabulary rather than against a list written here."""
    known = known_stage_senses()
    assert set(DEFAULT_WORKFLOW.rungs) <= known
    assert "merged" in known, "merged exists and is ambiguous, which is why it is not a rung"


def test_the_default_does_not_start_at_acquisition() -> None:
    """A workflow whose observation rung never fires places every file as a derivation.

    An acquisition stage is read from an instrument's own extension, so in a laboratory with
    no instrument files a first rung of ``acquisition`` would never fire at all.
    """
    assert DEFAULT_WORKFLOW.observation_stage == "raw"
    assert "acquisition" not in DEFAULT_WORKFLOW.rungs


# ---------------------------------------------------------------------------
# The four guards.
# ---------------------------------------------------------------------------


def test_a_stage_may_not_appear_twice(project: Project) -> None:
    """Guard 1, with the counter-example of a file in both raw and derived.

    A stage that both precedes and follows itself has no logical context, because a file at
    that stage would be before and after where data enters at the same time.
    """
    with pytest.raises(WorkflowRefusedError, match="appears more than once"):
        check_workflow("looped", ["raw", "processed", "raw"], known_stages=STAGES)


def test_the_observation_rung_must_be_one_the_workflow_has() -> None:
    """Guard 2. With no anchor inside the workflow, nothing is before or after anything."""
    for marked in (-1, 2, 99):
        with pytest.raises(WorkflowRefusedError, match="where data enters"):
            check_workflow("adrift", ["raw", "analysis"], observation=marked, known_stages=STAGES)


def test_every_named_stage_must_be_one_the_dictionary_knows() -> None:
    """Guard 3. A rung no rule can read looks exactly like an empty workflow, and is a typo."""
    with pytest.raises(WorkflowRefusedError, match="not a stage the Dictionary knows"):
        check_workflow("typo", ["raw", "postprocesing"], known_stages=STAGES)


def test_a_workflow_of_one_rung_is_refused() -> None:
    """Guard 4. One rung makes every file an observation and no file a derivation."""
    assert MINIMUM_RUNGS == 2
    with pytest.raises(WorkflowRefusedError, match="at least 2 rungs"):
        check_workflow("single", ["raw"], known_stages=STAGES)
    with pytest.raises(WorkflowRefusedError, match="at least 2 rungs"):
        check_workflow("empty", [], known_stages=STAGES)


def test_a_workflow_needs_a_name_and_its_rungs_need_names() -> None:
    with pytest.raises(WorkflowRefusedError, match="a workflow is named"):
        check_workflow("  ", ["raw", "analysis"], known_stages=STAGES)
    with pytest.raises(WorkflowRefusedError, match="a rung names a stage"):
        check_workflow("gappy", ["raw", "  ", "analysis"], known_stages=STAGES)


def test_a_laboratory_whose_instrument_emits_processed_data_can_say_so() -> None:
    """The case of an instrument that emits processed data. The guards must not stand in its way.

    Where an instrument emits already-processed data, ``processed`` is the observation and
    ``raw`` may not appear at all. The same stage word then means a different logical context
    from the one it means in the default workflow, and that is correct.
    """
    theirs = check_workflow("their-scope", ["processed", "analysis", "pooled"], known_stages=STAGES)
    assert logical_context_of("processed", theirs) is LogicalContext.OBSERVATION
    assert logical_context_of("processed", DEFAULT_WORKFLOW) is LogicalContext.DERIVATION


# ---------------------------------------------------------------------------
# What a logical context is.
# ---------------------------------------------------------------------------


def test_a_stage_reads_as_the_rung_it_sits_on_relative_to_the_observation() -> None:
    planned = check_workflow(
        "planned", ["summary", "raw", "analysis"], observation=1, known_stages=STAGES
    )
    assert logical_context_of("summary", planned) is LogicalContext.INFORMATION
    assert logical_context_of("raw", planned) is LogicalContext.OBSERVATION
    assert logical_context_of("analysis", planned) is LogicalContext.DERIVATION


def test_a_stage_the_workflow_does_not_name_is_unplaced_rather_than_information() -> None:
    """A file the engine cannot place stays unplaced, not information.

    A file the engine cannot place is not thereby a plan or a protocol. Calling it information
    would force a conclusion onto the one file whose only known fact is that nothing placed it.
    """
    assert logical_context_of("acquisition", DEFAULT_WORKFLOW) is LogicalContext.UNPLACED
    assert logical_context_of(None, DEFAULT_WORKFLOW) is LogicalContext.UNPLACED
    assert logical_context_of("", DEFAULT_WORKFLOW) is LogicalContext.UNPLACED


def test_the_four_values_are_the_four_the_design_names() -> None:
    assert {value.value for value in LogicalContext} == {
        "observation",
        "derivation",
        "information",
        "unplaced",
    }


# ---------------------------------------------------------------------------
# A workflow is a Record decision.
# ---------------------------------------------------------------------------


def test_declaring_a_workflow_writes_one_record_event(project: Project) -> None:
    event = declare_workflow(
        project.path,
        DEFAULT_WORKFLOW,
        actor="robin",
        occurred_at=MOMENT,
        against_run_id=RUN,
    )
    assert event.kind is RecordEventKind.DEFINE
    assert event.target == "workflow:default"
    assert json.loads(event.payload)["rungs"] == list(DEFAULT_WORKFLOW.rungs)
    assert workflows_in_force(project.path) == (DEFAULT_WORKFLOW,)


def test_a_refused_workflow_writes_nothing(project: Project) -> None:
    """The reason the check runs before the append rather than after.

    The Record is append-only, so a malformed workflow accepted here could never be taken out
    again, only superseded, and everything it had already re-interpreted would stay
    re-interpreted.
    """
    with pytest.raises(WorkflowRefusedError):
        declare_workflow(
            project.path,
            Workflow("looped", ("raw", "processed", "raw")),
            actor="robin",
            occurred_at=MOMENT,
            against_run_id=RUN,
        )
    assert read_events(project.path) == ()
    assert workflows_in_force(project.path) == ()


def test_a_project_that_declared_nothing_has_no_workflow_rather_than_the_default(
    project: Project,
) -> None:
    """ "This laboratory never said" and "this laboratory said the default" are different facts.

    Returning the default for a project that declared nothing would make a laboratory whose
    instrument emits processed data look as though it had agreed to the opposite.
    """
    assert workflows_in_force(project.path) == ()


def test_a_withdrawal_takes_a_workflow_out_of_force_and_leaves_it_readable(
    project: Project,
) -> None:
    declare_workflow(
        project.path, DEFAULT_WORKFLOW, actor="robin", occurred_at=MOMENT, against_run_id=RUN
    )
    withdraw_workflow(
        project.path, "default", actor="robin", occurred_at=MOMENT, against_run_id=RUN
    )
    assert workflows_in_force(project.path) == ()
    assert [event.kind for event in read_events(project.path)] == [
        RecordEventKind.DEFINE,
        RecordEventKind.REJECT,
    ]


def test_a_later_declaration_replaces_an_earlier_one_of_the_same_name(
    project: Project,
) -> None:
    declare_workflow(
        project.path, DEFAULT_WORKFLOW, actor="robin", occurred_at=MOMENT, against_run_id=RUN
    )
    revised = Workflow("default", ("processed", "analysis"))
    declare_workflow(project.path, revised, actor="robin", occurred_at=MOMENT, against_run_id=RUN)
    assert workflows_in_force(project.path) == (revised,)
    assert len(read_events(project.path)) == 2


def test_several_workflows_can_be_in_force_at_once(project: Project) -> None:
    """Several per project from version one, which is what a shared drive needs."""
    for workflow in (DEFAULT_WORKFLOW, Workflow("imaging", ("raw", "analysis"))):
        declare_workflow(
            project.path, workflow, actor="robin", occurred_at=MOMENT, against_run_id=RUN
        )
    assert {workflow.name for workflow in workflows_in_force(project.path)} == {
        "default",
        "imaging",
    }


def test_a_workflow_decision_is_attributed_like_every_other(project: Project) -> None:
    """It goes through the same append, so it inherits the same four refusals."""
    from ingence.record.log import AppendError

    with pytest.raises(AppendError, match="names no actor"):
        declare_workflow(
            project.path, DEFAULT_WORKFLOW, actor="", occurred_at=MOMENT, against_run_id=RUN
        )


# ---------------------------------------------------------------------------
# What a change would cost.
# ---------------------------------------------------------------------------


def test_a_person_can_be_told_what_changing_their_workflow_would_re_interpret() -> None:
    """The warning belongs before the decision, not after it ("what this costs").

    A person editing their workflow re-interprets their pool, and the honest moment to say so
    is while they are still deciding.
    """
    theirs = check_workflow("theirs", ["processed", "analysis"], known_stages=STAGES)
    stages = ["raw", "raw", "processed", "analysis", None]
    moved = what_would_change(stages, DEFAULT_WORKFLOW, theirs)
    assert moved["observation to unplaced"] == 2
    assert moved["derivation to observation"] == 1
    assert "derivation to derivation" not in moved, "only what moved is counted"


def test_declaring_a_first_workflow_counts_from_unplaced() -> None:
    """Before any workflow, nothing is placed, so a first declaration moves everything it names."""
    moved = what_would_change(["raw", "analysis", "acquisition"], None, DEFAULT_WORKFLOW)
    assert moved == {"unplaced to derivation": 1, "unplaced to observation": 1}
