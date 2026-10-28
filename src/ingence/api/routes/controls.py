"""The workspace writes beside the contract's reads: stop, reject, workflows, areas and goals.

These workspace writes each sit behind the server's guard (``server.WORKSPACE_ROUTES``). None is
a decision about the data in the Record's sense, and none is pinned to a run, so each answers
while a job runs:

- ``POST /api/workspace/stop``: stop a job, keeping what it read or not.
- ``POST /api/assumptions/reject``: reject an assumption the run rests on. The rejection waits for
  Re-run like every other answer.
- ``POST /api/workflows``: declare a workflow (or, with ``dry_run``, see what it would change).
- ``POST /api/workflows/withdraw``: withdraw a declared workflow.
- ``POST /api/areas/kind``: say what an area's folder stands for.
- ``POST /api/goals``, ``POST /api/goals/remove`` and ``POST /api/goals/lower``: write a goal,
  remove one, and lower one to "at least n of m" words.

Each route validates its body strictly and refuses a malformed one with ``ValueError`` (the
server answers 400 in words). Otherwise it answers ``201`` with its final shape. Every route does
its work:

- The Stop writes the request that the worker reads (``jobs/ledger.request_stop``) and says the
  job's state.
- The area's kind is an answer written on the Record target ``area:<entry_id>``.
- ``POST /api/workflows`` checks a workflow as the Record's guards check one. It counts what
  declaring the workflow would re-interpret against the current run (``what_would_change``) and,
  unless ``dry_run``, writes a DEFINE.
- ``POST /api/workflows/withdraw`` counts the same for taking a workflow out of force and writes a
  REJECT. A REJECT is also how a suggested workflow is refused.
- The goals live in ``ingence.goals``, and the rejection of an assumption is recorded by
  ``api/routes/assumptions_reject.py``.

Each answer names its actor (``actor`` in the body, else the decider the server knows). A write
that nobody made is refused.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.core.schema import AreaKind

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ingence.api.apply import DefinitionApplier

__all__ = [
    "ACTOR_REFUSAL",
    "AREAS_KIND_ROUTE",
    "ASSUMPTION_REJECT_ROUTE",
    "GOALS_LOWER_ROUTE",
    "GOALS_REMOVE_ROUTE",
    "GOALS_ROUTE",
    "ROUTES",
    "STOP_ROUTE",
    "WORKFLOWS_ROUTE",
    "WORKFLOWS_WITHDRAW_ROUTE",
    "Context",
    "write",
]


@dataclass(frozen=True, slots=True)
class Context:
    """What a control may reach: the project, the server's applier, and who decides here."""

    project: Path | None = None
    applier: DefinitionApplier | None = None
    decider: str | None = None


STOP_ROUTE = "/api/workspace/stop"
ASSUMPTION_REJECT_ROUTE = "/api/assumptions/reject"
WORKFLOWS_ROUTE = "/api/workflows"
WORKFLOWS_WITHDRAW_ROUTE = "/api/workflows/withdraw"
AREAS_KIND_ROUTE = "/api/areas/kind"
GOALS_ROUTE = "/api/goals"
GOALS_REMOVE_ROUTE = "/api/goals/remove"
GOALS_LOWER_ROUTE = "/api/goals/lower"

#: Every control, in the order the pages show them.
ROUTES: tuple[str, ...] = (
    STOP_ROUTE,
    ASSUMPTION_REJECT_ROUTE,
    WORKFLOWS_ROUTE,
    WORKFLOWS_WITHDRAW_ROUTE,
    AREAS_KIND_ROUTE,
    GOALS_ROUTE,
    GOALS_REMOVE_ROUTE,
    GOALS_LOWER_ROUTE,
)

#: The most characters a goal's text, a workflow's name or a scope may have.
TEXT_MAXIMUM = 500
#: What a control says where the server has no project open, so nothing can be recorded.
NOT_YET = "This is not available in this build yet; nothing was recorded."

#: What a stage of a declared workflow may list, each a list of words.
_STAGE_LISTS = ("types", "folders", "names")


def _mapping(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        message = f"{what} is a mapping, not {type(value).__name__}"
        raise ValueError(message)
    return value


def _text(body: dict[str, Any], name: str, *, maximum: int = TEXT_MAXIMUM) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        message = f"{name} is a non-empty string"
        raise ValueError(message)
    if len(value) > maximum:
        message = f"{name} is at most {maximum} characters; {len(value)} were given"
        raise ValueError(message)
    return value.strip()


def _optional_text(body: dict[str, Any], name: str) -> str | None:
    if body.get(name) is None:
        return None
    return _text(body, name)


def _boolean(body: dict[str, Any], name: str, *, default: bool | None = None) -> bool:
    value = body.get(name, default)
    if not isinstance(value, bool):
        message = f"{name} is true or false"
        raise ValueError(message)
    return value


def _words(value: object, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        message = f"{what} is a list of words"
        raise ValueError(message)
    return list(value)


def _area_kind(body: dict[str, Any]) -> str:
    value = body.get("kind")
    kinds = [kind.value for kind in AreaKind]
    if not isinstance(value, str) or value not in kinds:
        message = f"kind is one of {', '.join(kinds)}"
        raise ValueError(message)
    return value


def _stages(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        message = "workflow.stages is a list of stages"
        raise ValueError(message)
    stages: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        stage = _mapping(item, f"workflow.stages[{index}]")
        read: dict[str, Any] = {"stage": _text(stage, "stage")}
        for name in _STAGE_LISTS:
            read[name] = _words(stage.get(name, []), f"workflow.stages[{index}].{name}")
        stages.append(read)
    return stages


def _identifier_of(name: str) -> str:
    """A workflow's identifier from its name: ``wf_`` and the name's words joined."""
    from ingence.record.workflow import workflow_identifier

    return workflow_identifier(name)


def _not_counted() -> dict[str, Any]:
    """What a workflow change would change where no project is open to count it against."""
    return {
        "stages_changed": 0,
        "files": 0,
        "experiments": 0,
        "said": "Nothing is counted without a project and a published run.",
    }


def _run_workflows(project: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """The current run's identifier and the workflows it stated, by payload."""
    from ingence.infer.workflow_suggest import read_workflows
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        if not catalog.has_run:
            return None, []
        run_id = catalog.query("SELECT run_id FROM run LIMIT 1").fetchone()
        return (str(run_id[0]) if run_id else None), read_workflows(catalog.run_directory)


def _said_moves(moved: dict[str, int]) -> str:
    """The transitions in plain words, one clause each: "66 files would lose their step
    (derivation); 198 would lose theirs (observation)". A file leaving the workflow loses its
    step, one entering it takes one, and one moving between steps moves from one to the other;
    only the first clause says "files"."""
    from ingence.record.workflow import LogicalContext

    unplaced = LogicalContext.UNPLACED.value
    clauses: list[str] = []
    lost = False
    for key, count in sorted(moved.items()):
        was, _, now = key.partition(" to ")
        one = count == 1
        counted = f"{count:,} {'file' if one else 'files'}" if not clauses else f"{count:,}"
        if now == unplaced:
            # "theirs" once a clause has said "their step".
            whose = ("its" if one else "theirs") if lost else ("its step" if one else "their step")
            clauses.append(f"{counted} would lose {whose} ({was})")
            lost = True
        elif was == unplaced:
            clauses.append(f"{counted} would take a step ({now})")
        else:
            clauses.append(f"{counted} would move from {was} to {now}")
    return "; ".join(clauses) + "."


def _what_would_change(project: Path | None, before: Any, after: Any) -> dict[str, Any]:
    """What declaring ``after`` in place of ``before`` (either None) would re-interpret, counted
    against the project's current run: the files whose logical context moves, by what it moves
    them to, and the experiments holding them."""
    from ingence.experiment_units import run_units
    from ingence.record.workflow import LogicalContext, logical_context_of, what_would_change
    from ingence.storage.catalog import open_catalog

    if project is None:
        return _not_counted()
    with open_catalog(project) as catalog:
        if not catalog.has_run:
            return _not_counted()
        rows = catalog.query(
            "SELECT c.subject, e.parent_entry_id, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject AND NOT e.is_deleted "
            "WHERE c.predicate LIKE 'classifies.%stage%' AND c.resolution_status = 'resolved'"
        ).fetchall()
        units = run_units(catalog).units
    # The Record's own arithmetic over the stages, one per claim (``what_would_change``); the
    # files and experiments moved are counted here beside it, since that count is of stages.
    stages = [str(stage) for _, _, stage in rows]
    if after is not None:
        moved = what_would_change(stages, before, after)
    else:
        leaving = Counter(
            logical_context_of(stage, before).value
            for stage in stages
            if before is not None
            and logical_context_of(stage, before) is not LogicalContext.UNPLACED
        )
        moved = {
            f"{was} to {LogicalContext.UNPLACED.value}": n for was, n in sorted(leaving.items())
        }
    files: set[str] = set()
    experiments: set[str] = set()
    for subject, parent, stage in rows:
        was = LogicalContext.UNPLACED if before is None else logical_context_of(str(stage), before)
        now = LogicalContext.UNPLACED if after is None else logical_context_of(str(stage), after)
        if was is now:
            continue
        files.add(str(subject))
        if parent is not None and str(parent) in units:
            experiments.add(units[str(parent)])
    said = _said_moves(moved) if moved else "No file's place in the workflow would change."
    return {
        "stages_changed": len(moved),
        "files": len(files),
        "experiments": len(experiments),
        "said": said,
    }


def _checked(declared: dict[str, Any], project: Path | None) -> Any:
    """The declared workflow through the Record's guards, refused in words."""
    from ingence.record.workflow import (
        WorkflowRefusedError,
        WorkflowStage,
        check_workflow,
        known_stage_senses,
    )

    stages = [
        WorkflowStage(
            stage=str(stage["stage"]),
            types=tuple(str(word).casefold() for word in stage["types"]),
            folders=tuple(str(word) for word in stage["folders"]),
            names=tuple(str(word) for word in stage["names"]),
        )
        for stage in declared["stages"]
    ]
    try:
        return check_workflow(
            declared["name"],
            [stage.stage for stage in stages],
            stages=stages,
            scope=declared["scope"],
            exempt=declared["exempt"],
            known_stages=known_stage_senses(project),
        )
    except WorkflowRefusedError as error:
        message = f"the workflow was refused: {error}"
        raise ValueError(message) from error


def _in_force(project: Path, identifier: str) -> Any:
    """The workflow of that identifier the current run applied, or None."""
    from ingence.record.workflow import Workflow

    _, stated = _run_workflows(project)
    for payload in stated:
        if payload.get("id") == identifier and payload.get("in_force"):
            return Workflow.from_payload(payload)
    return None


#: What a job's state is said to be where the ledger holds no such job.
NO_SUCH_JOB = "none"


def _stop(body: dict[str, Any], context: Context) -> dict[str, Any]:
    """A person's Stop: write the request that the worker's heartbeat reads.

    A job that has not started ends ``cancelled`` at once. A running job ends when its worker
    reads the request, or when the supervisor stops it after the phase's grace. ``keep`` asks to
    keep what the content step has read. ``stop_keeps`` is true where it will, which is while the
    job reads contents (``ingence.jobs.STOP_KEEPS``, the constant that ``/api/applying`` also
    reads). The pass then publishes what it has read, and the job goes on to apply the rules to
    it. In any other phase a Stop keeps nothing of the run, and ``said`` says which.
    """
    from ingence.jobs import STOP_KEEPS, JobState, phase_words
    from ingence.jobs.ledger import FINAL_STATES, JobLedger

    job_id = _text(body, "job_id")
    keep = _boolean(body, "keep")
    if context.project is None:
        return {
            "job_id": job_id,
            "keep": keep,
            "requested": False,
            "state": NO_SUCH_JOB,
            "stop_keeps": False,
            "said": "No project is open here, so there is no job to stop.",
            "recorded": False,
        }
    ledger = JobLedger(context.project)
    held = ledger.get(job_id)
    if held is None:
        return {
            "job_id": job_id,
            "keep": keep,
            "requested": False,
            "state": NO_SUCH_JOB,
            "stop_keeps": False,
            "said": f"There is no job {job_id} in this project; nothing was stopped.",
            "recorded": False,
        }
    if held.state in FINAL_STATES:
        return {
            "job_id": job_id,
            "keep": keep,
            "requested": False,
            "state": held.state.value,
            "stop_keeps": False,
            "said": f"That job has already ended ({held.state.value}); nothing was stopped.",
            "recorded": False,
        }
    after = ledger.request_stop(job_id, keep=keep) or held
    keeps = keep and STOP_KEEPS and held.phase == "dive"
    if after.state is JobState.CANCELLED:
        said = "The job had not started; it is stopped and will not run."
    elif keeps:
        said = (
            "Stopping reading contents; what was read so far is kept, and the rules are "
            "applied to it next."
        )
    else:
        said = f"Stopping {phase_words(held.phase)}; it ends within a few seconds."
        if keep:
            said += " Only reading contents keeps what was read; nothing of this run is kept."
    return {
        "job_id": job_id,
        "keep": keep,
        "requested": True,
        "state": after.state.value,
        "stop_keeps": keeps,
        "said": said,
        "recorded": True,
    }


def _reject(
    body: dict[str, Any], project: Path | None = None, decider: str | None = None
) -> dict[str, Any]:
    """Reject an assumption: the Record answer that reverses its class.

    The answer is written on the class handle through the writer of the one answer route, and it
    waits for Re-run like every other answer. ``actor`` is the body's, else the server's decider,
    as for ``/api/workflows``, so a page that posts the ``reject.body`` of the assumptions route
    (its ``id`` alone) is answered. With neither actor, the write is refused."""
    assumption_id = _text(body, "id")
    if project is None:
        return {
            "id": assumption_id,
            "kind": None,
            "answer": None,
            "waits_for_rerun": True,
            "said": NOT_YET,
            "recorded": False,
        }
    from ingence.api.routes.assumptions_reject import reject

    return reject(
        project,
        assumption_id,
        actor=_optional_text(body, "actor") or _actor_of(body, decider),
        note=_optional_text(body, "note"),
    )


#: What a write with no name, and no name set on this machine, is refused with.
ACTOR_REFUSAL = "Ingence needs your name to save this. Set your name first."


def _actor_of(body: dict[str, Any], decider: str | None) -> str:
    actor = str(body.get("actor") or decider or "").strip()
    if not actor:
        raise ValueError(ACTOR_REFUSAL)
    return actor


def _declare(body: dict[str, Any], project: Path | None, decider: str | None) -> dict[str, Any]:
    workflow = _mapping(body.get("workflow"), "workflow")
    name = _text(workflow, "name")
    declared: dict[str, Any] = {
        "name": name,
        "scope": _optional_text(workflow, "scope"),
        "stages": _stages(workflow.get("stages")),
        "exempt": _words(workflow.get("exempt", []), "workflow.exempt"),
        "id": _identifier_of(name),
        "origin": "declared",
    }
    dry_run = _boolean(body, "dry_run", default=False)
    checked = _checked(declared, project)
    if project is None:
        return {
            "workflow": declared,
            "what_would_change": _not_counted(),
            "dry_run": dry_run,
            "said": NOT_YET,
            "recorded": False,
        }
    before = _in_force(project, declared["id"])
    change = _what_would_change(project, before, checked)
    if dry_run:
        return {
            "workflow": declared,
            "what_would_change": change,
            "dry_run": True,
            "said": "Nothing was recorded: this is what declaring it would change at the next run.",
            "recorded": False,
        }
    from ingence.record.log import now
    from ingence.record.workflow import declare_workflow

    run_id, _ = _run_workflows(project)
    declare_workflow(
        project,
        checked,
        actor=_actor_of(body, decider),
        occurred_at=now(),
        against_run_id=run_id or "",
    )
    return {
        "workflow": declared,
        "what_would_change": change,
        "dry_run": False,
        "said": "Declared. It takes effect at the next run.",
        "recorded": True,
    }


def _withdraw(body: dict[str, Any], project: Path | None, decider: str | None) -> dict[str, Any]:
    identifier = _text(body, "id")
    if project is None:
        return {
            "id": identifier,
            "withdrawn": False,
            "what_would_change": _not_counted(),
            "dry_run": _boolean(body, "dry_run", default=False),
            "said": NOT_YET,
            "recorded": False,
        }
    from ingence.record.log import now
    from ingence.record.workflow import withdraw_workflow, workflows_in_force

    run_id, stated = _run_workflows(project)
    named = {payload.get("id"): payload for payload in stated}
    held = named.get(identifier)
    name = (
        str(held["name"])
        if held is not None
        else next((w.name for w in workflows_in_force(project) if w.identifier == identifier), None)
    )
    if name is None:
        message = f"no workflow of this run or of the Record has the identifier {identifier!r}"
        raise ValueError(message)
    change = _what_would_change(project, _in_force(project, identifier), None)
    dry_run = _boolean(body, "dry_run", default=False)
    if dry_run:
        return {
            "id": identifier,
            "withdrawn": False,
            "what_would_change": change,
            "dry_run": True,
            "said": (
                "Nothing was recorded: this is what withdrawing it would change at the next run."
            ),
            "recorded": False,
        }
    # A refused suggestion is refused by its chain, so a run that lengthens it stays refused.
    chain = (
        [str(stage) for stage in held.get("rungs") or ()]
        if held is not None and held.get("origin") == "suggested"
        else None
    )
    withdraw_workflow(
        project,
        name,
        actor=_actor_of(body, decider),
        occurred_at=now(),
        against_run_id=run_id or "",
        chain=chain,
    )
    return {
        "id": identifier,
        "withdrawn": True,
        "what_would_change": change,
        "dry_run": False,
        "said": "Withdrawn. It stops applying at the next run.",
        "recorded": True,
    }


def _actor(body: dict[str, Any], context: Context) -> str:
    """Who answers: the body's ``actor``, else the server's decider, else refused in words."""
    actor = str(body.get("actor") or "").strip()
    if actor:
        return actor
    if context.decider:
        return context.decider
    raise ValueError(ACTOR_REFUSAL)


def _area_kind_write(body: dict[str, Any], context: Context) -> dict[str, Any]:
    """A person's answer on what an area's folder stands for: one Record event on
    ``area:<entry_id>``. It is allowed during a scan, because it reads the current run, pinned or
    not."""
    from ingence.record.targets import area_target

    area_id = _text(body, "area_id")
    kind = _area_kind(body)
    target = area_target(area_id)
    if context.project is None:
        return {
            "area_id": area_id,
            "kind": kind,
            "answer_target": target,
            "said": "No project is open here, so nothing was recorded.",
            "recorded": False,
        }
    actor = _actor(body, context)
    from ingence.areas import write_area_kind
    from ingence.storage.commit import read_pointer

    if read_pointer(context.project) is None:
        return {
            "area_id": area_id,
            "kind": kind,
            "answer_target": target,
            "said": (
                "This project has no run yet, and an answer is written against a run; "
                "read the project first."
            ),
            "recorded": False,
        }
    event = write_area_kind(context.project, area_id, kind, actor=actor)
    return {
        "area_id": area_id,
        "kind": kind,
        "answer_target": event.target,
        "said": (
            f"Recorded: this folder stands for a {kind}. The pages read it at once; "
            "the rules read it at the next re-run."
        ),
        "recorded": True,
    }


def _goal(body: dict[str, Any], project: Path | None = None) -> dict[str, Any]:
    """Write a goal to the workspace. Allowed during a scan, because inference never reads a
    projection. ``actor`` is kept where given and is never required."""
    text = _text(body, "text")
    actor = _optional_text(body, "actor")
    if project is None:
        return {"goal": {"id": "goal_unsaved", "text": text}, "said": NOT_YET, "recorded": False}
    from ingence.goals import write_goal

    goal = write_goal(project, text, actor=actor)
    return {
        "goal": {"id": goal.id, "text": goal.text, **({"actor": goal.actor} if goal.actor else {})},
        "said": "The goal is written; the Goals page answers it from the current run.",
        "recorded": True,
    }


def _goal_lower(body: dict[str, Any], project: Path | None = None) -> dict[str, Any]:
    """Lower a goal to "at least n of m" (``at_least``: a whole number from 1), or back to every
    word (``at_least`` null); a workspace write, like writing the goal."""
    goal_id = _text(body, "id")
    at_least = body.get("at_least")
    if at_least is not None and (isinstance(at_least, bool) or not isinstance(at_least, int)):
        message = "at_least is a whole number of words, one or more, or null for every word"
        raise ValueError(message)
    if at_least is not None and at_least < 1:
        message = "at_least is a whole number of words, one or more, or null for every word"
        raise ValueError(message)
    if project is None:
        return {"id": goal_id, "at_least": at_least, "said": NOT_YET, "recorded": False}
    from ingence.goals import lower_goal

    try:
        goal = lower_goal(project, goal_id, at_least, actor=_optional_text(body, "actor"))
    except LookupError as missing:
        raise ValueError(str(missing)) from missing
    said = (
        f"The goal now takes an experiment matching at least {goal.at_least} of its words."
        if goal.at_least is not None
        else "The goal takes every one of its words again."
    )
    return {"id": goal.id, "at_least": goal.at_least, "said": said, "recorded": True}


def _goal_remove(body: dict[str, Any], project: Path | None = None) -> dict[str, Any]:
    goal_id = _text(body, "id")
    if project is None:
        return {"id": goal_id, "removed": False, "said": NOT_YET, "recorded": False}
    from ingence.goals import remove_goal

    removed = remove_goal(project, goal_id, actor=_optional_text(body, "actor"))
    return {
        "id": goal_id,
        "removed": removed,
        "said": "The goal is removed." if removed else "No goal of that identifier stands.",
        "recorded": removed,
    }


#: Each control by its route. Every handler takes the body and what the server knows (the
#: project, the applier, the decider).
_CONTEXT_HANDLERS: dict[str, Callable[[dict[str, Any], Context], dict[str, Any]]] = {
    STOP_ROUTE: _stop,
    AREAS_KIND_ROUTE: _area_kind_write,
    WORKFLOWS_ROUTE: lambda body, context: _declare(body, context.project, context.decider),
    WORKFLOWS_WITHDRAW_ROUTE: (
        lambda body, context: _withdraw(body, context.project, context.decider)
    ),
    ASSUMPTION_REJECT_ROUTE: lambda body, context: _reject(body, context.project, context.decider),
    GOALS_ROUTE: lambda body, context: _goal(body, context.project),
    GOALS_REMOVE_ROUTE: lambda body, context: _goal_remove(body, context.project),
    GOALS_LOWER_ROUTE: lambda body, context: _goal_lower(body, context.project),
}


def write(route: str, body: object, *, context: Context | None = None) -> dict[str, Any]:
    """Answer one control's write: its shape, and what it recorded.

    A malformed body is refused with :class:`ValueError`, which the server answers 400 in words;
    a route no control owns is a :class:`LookupError` (the server names its routes first).
    ``context`` is what the server knows (the project, the applier, the decider).
    """
    handler = _CONTEXT_HANDLERS.get(route)
    if handler is None:
        message = f"no control at {route}"
        raise LookupError(message)
    return handler(_mapping(body, "the body"), context or Context())
