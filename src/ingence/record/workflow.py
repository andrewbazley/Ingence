"""The declared workflow: where data enters a project, and what follows from that.

A logical context is not a property of a stage. It is a function of (stage, workflow). In the
default workflow, ``raw`` is where data enters and everything after it is a derivation. In a
laboratory whose instrument emits already-processed data, ``processed`` is the observation and
``raw`` may not appear at all. The same stage word means a different logical context in two
projects. That is correct, because the workflow says where data enters.

Three rungs can be read off any workflow with no extra declaration:

============================ ================= ================================================
Where a stage sits           Logical context   Why
============================ ================= ================================================
Before the observation rung  ``information``   Plans, protocols, designs. Real, and not
                                               grounded in data that exists yet
At the observation rung      ``observation``   Where data enters. "An observation without
                                               followup"
After it                     ``derivation``    Exists in service of what entered
Not in the workflow at all   ``unplaced``      Not ``information``, which would be forcing a
                                               conclusion
============================ ================= ================================================

A workflow is a Record decision and not a setting. Which stages a laboratory works through is an
interpretive commitment, and every interpretive commitment here is attributable and reversible. The
workflow is one of the four inputs of a reproducible interpretation, so changing it re-interprets
the pool reproducibly and without re-scanning anything.

The guards refuse and do not warn, and this is the only place they can act. A cyclic workflow is a
malformed declaration and not an uncertain one. So it belongs with the Rulebook loader's refusals,
such as two revisions sharing a number or a parameter that the sentence hides, and not with
abstention. There is nothing to abstain about. The rule ``derivation_is_acyclic`` is declared as a
constraining rule and is not evaluable. It constrains a decoder among candidate edges, and no
decoder is built. A cyclic workflow would generate cyclic derivation candidates, and nothing
downstream would notice. Catching the cycle where a person writes it is earlier, and today it is the
only option.

The shipped default passes its own guards, and a test asserts it. A guard that the default violates
is a guard nobody will trust.

A file's logical context changes when the workflow changes. That is correct and reproducible. It
means that a person who edits their workflow re-interprets their pool, and they should be told
before they commit to the edit and not after. :func:`what_would_change` does that.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import RecordEventKind
from ingence.record.log import append_event, latest_by_target, read_events

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from pathlib import Path

    from ingence.core.ids import Scope
    from ingence.core.schema import RecordEvent

__all__ = [
    "DEFAULT_WORKFLOW",
    "MINIMUM_RUNGS",
    "ORIGINS",
    "TARGET_PREFIX",
    "LogicalContext",
    "Refusals",
    "Workflow",
    "WorkflowEvidence",
    "WorkflowRefusedError",
    "WorkflowStage",
    "check_workflow",
    "declare_workflow",
    "known_stage_senses",
    "logical_context_of",
    "what_would_change",
    "withdraw_workflow",
    "workflow_identifier",
    "workflow_target",
    "workflows_in_force",
    "workflows_refused",
]

#: Where a workflow came from: suggested by the engine from the chains it saw, or declared by a
#: person.
ORIGINS = ("declared", "suggested")

#: A workflow of one rung makes every file an observation and no file a derivation, which is
#: not a workflow but a label.
MINIMUM_RUNGS = 2

#: Prefix on a workflow's target, so a Record reader tells a workflow decision from an answer
#: to a question without parsing the rest.
TARGET_PREFIX = "workflow"


class LogicalContext(StrEnum):
    """Which of three worlds a file is in, read relative to a workflow.

    The values are not ``raw`` and ``analysis``. Both of those are already stage senses, and a file
    can sit at the ``raw`` stage while being somebody's analysis output. A processed table named
    ``..._raw_counts.csv`` is raw input to the next step and a derivation of the imaging. The stage
    says where the file sits in a pipeline. The logical context says which world it is in.
    """

    #: Where data enters: an observation without followup.
    OBSERVATION = "observation"
    #: Exists in service of what entered. It cannot exist before or without raw data.
    DERIVATION = "derivation"
    #: Related to the research and not grounded in data that exists yet: plans, protocols.
    INFORMATION = "information"
    #: The engine cannot place it. Not information, which would be forcing a conclusion on a file
    #: whose only known fact is that nothing placed it.
    UNPLACED = "unplaced"


@dataclass(frozen=True, slots=True)
class WorkflowStage:
    """One stage of a workflow and what puts a file at it (payload v2).

    The file types (their spellings, ``nd2``, ``csv``), the folder names (a word, or a glob) and
    the name patterns (globs) that place a file at the stage. Each may be empty: a v1 workflow
    names its rungs and nothing else, and ``stage_from_workflow`` then places nothing by it.
    """

    stage: str
    types: tuple[str, ...] = ()
    folders: tuple[str, ...] = ()
    names: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "types": list(self.types),
            "folders": list(self.folders),
            "names": list(self.names),
        }

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> WorkflowStage:
        return cls(
            stage=str(raw["stage"]),
            types=_words(raw.get("types")),
            folders=_words(raw.get("folders")),
            names=_words(raw.get("names")),
        )


@dataclass(frozen=True, slots=True)
class WorkflowEvidence:
    """Why a workflow stands: the experiments following it, and the chains of stages seen."""

    experiments: int = 0
    #: Each chain of stages seen, with how many experiments follow it, most first.
    chains: tuple[tuple[tuple[str, ...], int], ...] = ()
    #: The experiments (their folders' entries) whose chains were counted, each with the chain
    #: it reached, so a rule reading the workflow applies it to their files, at those rungs, and
    #: never project-wide. Empty for a declared one.
    followed: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "experiments": self.experiments,
            "chains": [
                {"chain": list(chain), "experiments": count} for chain, count in self.chains
            ],
            "followed": {experiment: list(chain) for experiment, chain in self.followed},
        }

    @classmethod
    def from_payload(cls, raw: object) -> WorkflowEvidence:
        if not isinstance(raw, Mapping):
            return cls()
        chains: list[tuple[tuple[str, ...], int]] = []
        for item in raw.get("chains") or ():
            if isinstance(item, Mapping) and isinstance(item.get("chain"), list):
                chains.append((_words(item["chain"]), int(item.get("experiments") or 0)))
        held = raw.get("followed")
        followed = (
            tuple(
                (str(experiment), _words(chain))
                for experiment, chain in sorted(held.items())
                if isinstance(chain, list)
            )
            if isinstance(held, Mapping)
            else ()
        )
        return cls(
            experiments=int(raw.get("experiments") or 0), chains=tuple(chains), followed=followed
        )


def _words(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def workflow_identifier(name: str) -> str:
    """A workflow's identifier from its name: ``wf_`` and the name's words joined (the contract's
    spelling, as ``POST /api/workflows`` answers it)."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    return "wf_" + "_".join(words or ["workflow"])


@dataclass(frozen=True, slots=True)
class Workflow:
    """One laboratory's path from where data enters to what it becomes, declared or suggested.

    Payload v2 keeps ``rungs``, so v1 still reads: the rungs are the stages' names in order, and a
    v1 workflow is one whose stages carry no types, folders or names. ``scope`` is the root or
    folder prefix it applies to (None for every area), ``exempt`` the name patterns it does not
    stage, ``origin`` who made it, and ``evidence`` why it stands.
    """

    name: str
    #: The stages in order, each a stage sense the Dictionary knows.
    rungs: tuple[str, ...]
    #: Which rung data enters at. The first unless a person marked another, because an
    #: instrument writing into a folder is the ordinary case and a laboratory whose
    #: instrument emits processed data is the one that has to say so.
    observation: int = 0
    scope: str | None = None
    stages: tuple[WorkflowStage, ...] = ()
    exempt: tuple[str, ...] = ()
    origin: str = "declared"
    evidence: WorkflowEvidence = field(default_factory=WorkflowEvidence)

    @property
    def identifier(self) -> str:
        """The contract's identifier for this workflow."""
        return workflow_identifier(self.name)

    @property
    def observation_stage(self) -> str:
        """The stage where data enters this workflow."""
        return self.rungs[self.observation]

    @property
    def entering_types(self) -> tuple[str, ...]:
        """The file types (spellings) at the rung where data enters; empty for a v1 workflow."""
        for stage in self.stages:
            if stage.stage == self.observation_stage:
                return stage.types
        return ()

    def stage_named(self, stage: str) -> WorkflowStage | None:
        """The stage of that name, or None where the workflow has no such rung."""
        return next((held for held in self.stages if held.stage == stage), None)

    def as_payload(self) -> dict[str, Any]:
        """The canonical mapping the Record carries: v1's three fields, then v2's."""
        return {
            "name": self.name,
            "rungs": list(self.rungs),
            "observation": self.observation,
            "scope": self.scope,
            "stages": [stage.as_payload() for stage in self.stages],
            "exempt": list(self.exempt),
            "origin": self.origin,
            "evidence": self.evidence.as_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Workflow:
        """A workflow from a v1 payload (``rungs``) or a v2 one (``stages`` beside them)."""
        stages = tuple(
            WorkflowStage.from_payload(item)
            for item in payload.get("stages") or ()
            if isinstance(item, Mapping) and item.get("stage")
        )
        rungs = _words(payload.get("rungs")) or tuple(stage.stage for stage in stages)
        origin = str(payload.get("origin") or "declared")
        return cls(
            name=str(payload["name"]),
            rungs=rungs,
            observation=int(payload.get("observation", 0)),
            scope=(str(payload["scope"]) if payload.get("scope") else None),
            stages=stages,
            exempt=_words(payload.get("exempt")),
            origin=origin if origin in ORIGINS else "declared",
            evidence=WorkflowEvidence.from_payload(payload.get("evidence")),
        )


#: The shipped default: raw, processed, analysis.
#:
#: The default is domain-neutral, and it deliberately does not start at ``acquisition``. An
#: acquisition stage is read from an instrument's own extension. A laboratory with no instrument
#: files would have a first rung that never fires, and a workflow whose observation rung never fires
#: places every file as a derivation of nothing.
#:
#: ``pooled`` is a stage the Dictionary holds, and a project may declare it as a rung. It is
#: unambiguous, unlike ``merged``, which is ambiguous between a merged image and merged results. An
#: analysis that rests on several independent replicates is a different kind of thing from an
#: analysis of one, and a laboratory that writes the difference declares the rung. The default
#: leaves it out, because on real laboratory folders no file reached that rung.
DEFAULT_WORKFLOW = Workflow(name="default", rungs=("raw", "processed", "analysis"))


class WorkflowRefusedError(Exception):
    """A declaration was refused, and the Record is unchanged.

    Refused rather than warned: a workflow that cannot say which way derivation runs is
    malformed, and nothing downstream would catch it.
    """


def workflow_target(name: str) -> str:
    """Return the canonical target of a decision about one workflow."""
    return f"{TARGET_PREFIX}:{name}"


def known_stage_senses(project: Path | None = None) -> frozenset[str]:
    """Return every stage sense the Dictionary holds, the project's own answers included.

    A rung that a person defined is a rung the guard must know. A stage sense stated in the Record
    is as real as one that ships, and refusing a workflow for naming it would tell somebody their
    own vocabulary is a typo. Without a project, this is the shipped vocabulary, which is what a
    caller that declares a workflow outside any project has.

    The vocabulary is imported inside the function, so that nothing that merely reads a workflow
    pays for loading it, as in :func:`ingence.infer.run._vocabulary`.
    """
    from ingence.dictionary.store import DictionaryStore

    store = DictionaryStore.builtin() if project is None else DictionaryStore.for_project(project)
    return frozenset(sense.term for sense in store.senses() if sense.theme_kind == "stage")


def check_workflow(
    name: str,
    rungs: Sequence[str],
    *,
    observation: int = 0,
    known_stages: Collection[str] | None = None,
    stages: Sequence[WorkflowStage] = (),
    scope: str | None = None,
    exempt: Sequence[str] = (),
    origin: str = "declared",
    evidence: WorkflowEvidence | None = None,
) -> Workflow:
    """Return the workflow, or refuse it. The four guards, in order.

    ``known_stages`` is the vocabulary that the rungs must be drawn from. It defaults to the shipped
    stage senses. It is an argument so that a project's own vocabulary can be handed in the day one
    is loaded, and this function does not grow a second way to find it.

    A v2 workflow names its stages with what puts a file at each. ``stages`` are given in order, and
    their names are the rungs where ``rungs`` is empty. The two must agree where both are given.
    ``scope``, ``exempt``, ``origin`` and ``evidence`` travel with the workflow.
    """
    if not name.strip():
        message = "a workflow is named, so that a person can say which one they meant"
        raise WorkflowRefusedError(message)
    if origin not in ORIGINS:
        message = f"a workflow is {' or '.join(ORIGINS)}, not {origin!r}"
        raise WorkflowRefusedError(message)
    held = tuple(stages)
    if held and not rungs:
        rungs = [stage.stage for stage in held]
    if held and [stage.stage for stage in held] != [str(rung).strip() for rung in rungs]:
        message = f"{name!r} names its rungs and its stages differently; they are one list"
        raise WorkflowRefusedError(message)

    named = [str(rung).strip() for rung in rungs]
    if any(not rung for rung in named):
        message = "a rung names a stage; a blank rung is a gap nothing can be placed against"
        raise WorkflowRefusedError(message)

    # Guard 4, first because the other three read better against a workflow that is one.
    if len(named) < MINIMUM_RUNGS:
        message = (
            f"a workflow has at least {MINIMUM_RUNGS} rungs and {name!r} has {len(named)}. "
            "One rung makes every file an observation and no file a derivation, so nothing "
            "is before or after anything."
        )
        raise WorkflowRefusedError(message)

    # Guard 1: a stage appears at most once. The direct loop.
    seen = [rung for index, rung in enumerate(named) if rung in named[:index]]
    if seen:
        message = (
            f"{seen[0]!r} appears more than once in {name!r}. A stage that both precedes and "
            "follows itself has no logical context: a file at that stage would be before and "
            "after where data enters at the same time, which is the loop that costs a file "
            "its directionality."
        )
        raise WorkflowRefusedError(message)

    # Guard 2: exactly one rung is the observation, and it is a rung this workflow has.
    if not 0 <= observation < len(named):
        message = (
            f"{name!r} marks rung {observation} as where data enters and has "
            f"{len(named)} rungs. With no anchor inside the workflow, nothing is before or "
            "after anything."
        )
        raise WorkflowRefusedError(message)

    # Guard 3: every named stage is a stage sense the Dictionary knows.
    vocabulary = known_stage_senses() if known_stages is None else set(known_stages)
    unknown = [rung for rung in named if rung not in vocabulary]
    if unknown:
        message = (
            f"{unknown[0]!r} is not a stage the Dictionary knows, so no rule could ever read "
            f"that rung of {name!r}. A workflow full of names nothing matches looks exactly "
            "like an empty workflow, and is usually a typo."
        )
        raise WorkflowRefusedError(message)

    return Workflow(
        name=name.strip(),
        rungs=tuple(named),
        observation=observation,
        scope=(scope.strip() or None) if isinstance(scope, str) else None,
        stages=held,
        exempt=tuple(str(pattern) for pattern in exempt if str(pattern)),
        origin=origin,
        evidence=evidence if evidence is not None else WorkflowEvidence(),
    )


def logical_context_of(stage: str | None, workflow: Workflow) -> LogicalContext:
    """Return which world a stage sits in, relative to one workflow.

    A stage the workflow does not name is unplaced rather than information. A file the engine cannot
    place is not thereby a plan or a protocol, and calling it one would be forcing a conclusion on
    the one file whose only known fact is that nothing placed it.

    Two readings outrank this wherever they apply and neither is decided here, because both
    are facts about a file rather than about a workflow: an instrument's own extension is an
    observation whatever the workflow says, and a program is a derivation by construction.
    Those belong to the classifying rules that read this.
    """
    if not stage:
        return LogicalContext.UNPLACED
    try:
        index = workflow.rungs.index(stage)
    except ValueError:
        return LogicalContext.UNPLACED
    if index < workflow.observation:
        return LogicalContext.INFORMATION
    if index == workflow.observation:
        return LogicalContext.OBSERVATION
    return LogicalContext.DERIVATION


def declare_workflow(
    project: Path,
    workflow: Workflow,
    *,
    actor: str,
    occurred_at: str,
    against_run_id: str,
    scope: Scope = PROJECT_SCOPE,
    known_stages: Collection[str] | None = None,
) -> RecordEvent:
    """Check a workflow and write it to the Record, or refuse it and write nothing.

    The check comes before the write. This is what "refused at declaration" means. The Record is
    append-only, so a malformed workflow accepted here could never be taken out again, only
    superseded, and everything it had already re-interpreted would stay re-interpreted.

    The workflow is declared against a run, as every decision is. A decision is read in the light of
    what the person was looking at, and at ``init`` there is nothing to look at. No pool has been
    enumerated, so there is no evidence that a workflow could be a commitment about. Asking at
    ``init`` and writing after the first index keeps both halves.
    """
    checked = check_workflow(
        workflow.name,
        workflow.rungs,
        observation=workflow.observation,
        # The project's own vocabulary, so a rung that a person defined in the Record is a rung the
        # guard knows. A caller that names the stages itself still overrides it.
        known_stages=known_stage_senses(project) if known_stages is None else known_stages,
        stages=workflow.stages,
        scope=workflow.scope,
        exempt=workflow.exempt,
        # What the Record holds is a person's: a suggestion edited and written is declared.
        origin="declared",
        evidence=workflow.evidence,
    )
    return append_event(
        project,
        kind=RecordEventKind.DEFINE,
        actor=actor,
        occurred_at=occurred_at,
        target=workflow_target(checked.name),
        against_run_id=against_run_id,
        payload=checked.as_payload(),
        scope=scope,
    )


def withdraw_workflow(
    project: Path,
    name: str,
    *,
    actor: str,
    occurred_at: str,
    against_run_id: str,
    scope: Scope = PROJECT_SCOPE,
    chain: Sequence[str] | None = None,
) -> RecordEvent:
    """Take a workflow out of force by appending, never by removing.

    The declaration stays in the log and stays readable, because a person who changes how they
    describe their own work has made a decision worth keeping.

    A refused suggestion is refused by its chain. ``chain`` names the suggested stages. A suggestion
    whose chain begins with them stays refused, whatever its name was spelled from. This holds for
    this run's suggestion and for a later run's suggestion that lengthened the chain.
    """
    payload: dict[str, Any] = {"name": name}
    if chain:
        payload["chain"] = [str(stage) for stage in chain]
    return append_event(
        project,
        kind=RecordEventKind.REJECT,
        actor=actor,
        occurred_at=occurred_at,
        target=workflow_target(name),
        against_run_id=against_run_id,
        payload=payload,
        scope=scope,
    )


def workflows_in_force(project: Path) -> tuple[Workflow, ...]:
    """Return the workflows a project has declared and not withdrawn, in name order.

    The function replays the Record. The last decision about each workflow is the one that stands,
    and a withdrawal is a decision like any other. A project that has declared none has none. It
    does not get the default, because "this laboratory never said" and "this laboratory said the
    default" are different facts, and the caller decides what to do about it.
    """
    found: list[Workflow] = []
    for target, event in sorted(latest_by_target(read_events(project)).items()):
        if not target.startswith(f"{TARGET_PREFIX}:"):
            continue
        if event.kind is not RecordEventKind.DEFINE:
            continue
        found.append(Workflow.from_payload(json.loads(event.payload)))
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Refusals:
    """What a person refused: workflows by identifier, and suggested chains by their stages."""

    identifiers: frozenset[str] = frozenset()
    #: Each refused chain; a suggestion whose rungs begin with one is refused.
    chains: tuple[tuple[str, ...], ...] = ()

    def refuses(self, workflow: Workflow) -> bool:
        """Whether the workflow is refused: by its identifier, or by a chain its rungs begin
        with (a chain that lengthens in a later run stays refused)."""
        if workflow.identifier in self.identifiers:
            return True
        return any(
            chain and tuple(workflow.rungs[: len(chain)]) == tuple(chain) for chain in self.chains
        )

    @classmethod
    def of(cls, held: object) -> Refusals:
        """Refusals from what a caller holds: a Refusals, or identifiers alone."""
        if isinstance(held, cls):
            return held
        if isinstance(held, (list, tuple, set, frozenset)):
            return cls(identifiers=frozenset(str(item) for item in held))
        return cls()


def workflows_refused(project: Path) -> Refusals:
    """The workflows a person refused and did not declare again.

    A suggested workflow is in force until refused. Refusing writes a REJECT on its target, and the
    last decision about the target stands, so a later DEFINE puts a workflow of that name back. A
    refusal that named the suggested chain refuses every suggestion whose rungs begin with it. Read
    beside :func:`workflows_in_force`, which lists the DEFINEs.
    """
    identifiers: set[str] = set()
    chains: list[tuple[str, ...]] = []
    for target, event in latest_by_target(read_events(project)).items():
        if not target.startswith(f"{TARGET_PREFIX}:") or event.kind is not RecordEventKind.REJECT:
            continue
        identifiers.add(workflow_identifier(target.removeprefix(f"{TARGET_PREFIX}:")))
        payload = json.loads(event.payload) if event.payload else {}
        chain = payload.get("chain") if isinstance(payload, dict) else None
        if isinstance(chain, list) and chain:
            chains.append(tuple(str(stage) for stage in chain))
    return Refusals(identifiers=frozenset(identifiers), chains=tuple(sorted(set(chains))))


def what_would_change(
    stages: Collection[str | None], before: Workflow | None, after: Workflow
) -> dict[str, int]:
    """Count how many files a workflow change re-interprets, by what it changes them to.

    The warning belongs before the decision and not after it. A person who edits their workflow
    re-interprets their pool. The moment to say "this changes the logical context of 1,200 files" is
    while they are still deciding. The function takes the stages as the Index holds them, so the
    caller owns the query and this function owns the arithmetic.
    """
    moved: dict[str, int] = {}
    for stage in stages:
        was = LogicalContext.UNPLACED if before is None else logical_context_of(stage, before)
        now = logical_context_of(stage, after)
        if was is not now:
            moved[f"{was.value} to {now.value}"] = moved.get(f"{was.value} to {now.value}", 0) + 1
    return dict(sorted(moved.items()))
