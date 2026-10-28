"""The workflow suggester: a fixed phase of inference, deterministic, from the rules' results.

The earlier phases run the existing rules, through experiments and derivation candidates. This
module then reads each experiment's files and takes the chain of stages its files reached and the
file types at each. It counts the chains across experiments, so a laboratory whose experiments go
``nd2`` to ``tif`` to ``csv`` in thirty-one of them is told so in one sentence. After the suggester,
``stage_from_workflow`` places each file by the workflow in force, and the raw
anchor counts the workflow's entering types as acquisitions. Everything stays inside one inference
run, reproducible from Index, Rulebook, Dictionary and Record. Nothing feeds back from a readout to
the next run.

A suggestion is in force as assumed until refused. A person's declared workflow of the same
identifier replaces it. A refusal (``POST /api/workflows/withdraw`` on a suggested one) writes a
REJECT that the next run reads, and the suggestion is listed and not applied. The run reads both
at its Record watermark, as it reads every decision.

What the evidence is: a file's stage is the furthest stage its resolved stage claims name (``raw``
and ``acquisition`` are one rung, the one completeness counts), or its folder's stage word where it
has none of its own. Its type is its extension's spelling. The type is counted for a rung only
where the file's own claims placed it there. A folder's stage word says the chain reaches the
rung, not which types sit at it, and counting every file of a ``results`` folder as analysis would
put an archive and a protocol there. An experiment's chain is its stages in the order of
:data:`STAGE_ORDER`. The experiments a workflow was counted from travel with its evidence, so the
rule reading it applies it to their files alone. Chains that skip a step of a longer chain belong
to that chain's workflow (``acquisition, analysis`` is ``acquisition, processed, analysis`` with
the middle skipped). A workflow stands where ``minimum_experiments`` or more experiments follow
it. Nothing here reads a file, a clock or the Record.

The run says what it suggested in a file of its own directory (:data:`WORKFLOWS_FILE`). The
workflows route reads that file, and a dive carries it forward beside the claims it carries.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from ingence.core.schema import EntryType
from ingence.core.tiers import ResolutionStatus
from ingence.record.workflow import (
    Refusals,
    Workflow,
    WorkflowEvidence,
    WorkflowStage,
    check_workflow,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.infer.subjects import EntryRecords

__all__ = [
    "SAME_RUNG",
    "STAGE_ORDER",
    "WORKFLOWS_FILE",
    "carry_workflows",
    "chains_of_experiments",
    "read_workflows",
    "suggest_workflows",
    "workflows_in_force_for",
    "write_workflows",
]

#: The run's own statement of the workflows it suggested and the declared ones it read.
WORKFLOWS_FILE = "workflows.json"

#: The order of the stages a chain is written in, earliest first.
STAGE_ORDER: tuple[str, ...] = (
    "acquisition",
    "preprocessed",
    "intermediate",
    "processed",
    "quantification",
    "analysis",
    "pooled",
    "summary",
    "report",
    "export",
)

#: Two stage words that name one rung: a recording is where data enters, by its format or by
#: the word ``raw``, and completeness counts the rung by the format rule's word. This is the one
#: copy. The step, the story and the scorer import it with :data:`STAGE_ORDER`.
SAME_RUNG: dict[str, str] = {"raw": "acquisition"}

#: What a stage claim's predicate holds (``classifies.<rule id>``, and every stage rule's
#: identifier holds the word), as completeness reads them.
_STAGE_MARK = "stage"


def _stage_of(records: EntryRecords) -> str | None:
    """The furthest stage the entry's own resolved stage claims name, in the chain's order."""
    found: list[str] = []
    for claim in records.claims:
        if (
            claim.predicate.startswith("classifies.")
            and _STAGE_MARK in claim.predicate
            and str(claim.resolution_status) == str(ResolutionStatus.RESOLVED.value)
            and claim.object_reference
        ):
            stage = SAME_RUNG.get(claim.object_reference, claim.object_reference)
            if stage in STAGE_ORDER:
                found.append(stage)
    if not found:
        return None
    return max(found, key=STAGE_ORDER.index)


def _folder_stage(records: EntryRecords) -> str | None:
    """The stage a folder's own name states, through its bindings; None where it states none."""
    found: list[str] = []
    for binding in records.bindings:
        if (
            binding.constraint is None
            and binding.resolution_status is ResolutionStatus.RESOLVED
            and binding.theme_kind == "stage"
            and binding.term is not None
        ):
            stage = SAME_RUNG.get(binding.term, binding.term)
            if stage in STAGE_ORDER:
                found.append(stage)
    return max(found, key=STAGE_ORDER.index) if found else None


def _type_of(records: EntryRecords) -> str | None:
    extension = records.entry.compound_extension or records.entry.extension
    return extension.casefold() if extension else None


def chains_of_experiments(
    records: Sequence[EntryRecords], experiment_of: Mapping[str, str]
) -> dict[str, dict[str, tuple[set[str], set[str]]]]:
    """Each experiment's stages, and at each the file types and the folder names seen.

    ``experiment_of`` maps each file's entry to its experiment folder's entry. A file with no
    stage of its own takes its folder's stage word; a file with neither is not in the chain.
    """
    folders = {
        held.entry.entry_id: held
        for held in records
        if held.entry.entry_type is EntryType.DIRECTORY
    }
    chains: dict[str, dict[str, tuple[set[str], set[str]]]] = {}
    for held in records:
        if held.entry.entry_type is EntryType.DIRECTORY:
            continue
        experiment = experiment_of.get(held.entry.entry_id)
        if experiment is None:
            continue
        parent = folders.get(held.entry.parent_entry_id or "")
        folder_stage = _folder_stage(parent) if parent is not None else None
        own = _stage_of(held)
        stage = own or folder_stage
        if stage is None:
            continue
        types, names = chains.setdefault(experiment, {}).setdefault(stage, (set(), set()))
        kind = _type_of(held)
        if kind and own is not None:
            types.add(kind)
        if parent is not None and folder_stage == stage:
            names.add(parent.entry.name.casefold())
    return chains


def _is_subsequence(shorter: Sequence[str], longer: Sequence[str]) -> bool:
    position = 0
    for stage in longer:
        if position < len(shorter) and shorter[position] == stage:
            position += 1
    return position == len(shorter)


def suggest_workflows(
    records: Sequence[EntryRecords],
    experiment_of: Mapping[str, str],
    *,
    minimum_experiments: int = 2,
) -> tuple[Workflow, ...]:
    """The workflows the experiments' chains suggest, most experiments first.

    The result is deterministic: the same records and experiments give the same workflows in the
    same order. A chain of one stage suggests nothing (one rung is a label, not a workflow). A chain
    that skips steps of a longer one is counted under the longer one's workflow.
    ``minimum_experiments`` is the setting ``infer.workflow_minimum_experiments``, handed in by the
    pass.
    """
    chains = chains_of_experiments(records, experiment_of)
    by_chain: Counter[tuple[str, ...]] = Counter()
    for stages in chains.values():
        chain = tuple(stage for stage in STAGE_ORDER if stage in stages)
        if len(chain) >= 2:
            by_chain[chain] += 1
    # Longest first, then most followed, then by name: the chain the others are steps of.
    ordered = sorted(by_chain, key=lambda chain: (-len(chain), -by_chain[chain], chain))
    heads: list[tuple[tuple[str, ...], list[tuple[str, ...]]]] = []
    for chain in ordered:
        for head, members in heads:
            if _is_subsequence(chain, head):
                members.append(chain)
                break
        else:
            heads.append((chain, [chain]))
    found: list[Workflow] = []
    for head, members in heads:
        experiments = sum(by_chain[member] for member in members)
        if experiments < minimum_experiments:
            continue
        types: dict[str, set[str]] = {stage: set() for stage in head}
        folders: dict[str, set[str]] = {stage: set() for stage in head}
        followed: list[tuple[str, tuple[str, ...]]] = []
        for experiment, stages in chains.items():
            chain = tuple(stage for stage in STAGE_ORDER if stage in stages)
            if chain not in members:
                continue
            followed.append((experiment, chain))
            for stage, (kinds, names) in stages.items():
                types[stage].update(kinds)
                folders[stage].update(names)
        evidence = WorkflowEvidence(
            experiments=experiments,
            chains=tuple(
                (member, by_chain[member])
                for member in sorted(members, key=lambda m: (-by_chain[m], m))
            ),
            followed=tuple(sorted(followed)),
        )
        found.append(
            check_workflow(
                " then ".join(head),
                head,
                known_stages=head,
                stages=[
                    WorkflowStage(
                        stage=stage,
                        types=tuple(sorted(types[stage])),
                        folders=tuple(sorted(folders[stage])),
                    )
                    for stage in head
                ],
                origin="suggested",
                evidence=evidence,
            )
        )
    return tuple(
        sorted(found, key=lambda workflow: (-workflow.evidence.experiments, workflow.name))
    )


def _covers(declared: Workflow, suggested: Workflow) -> bool:
    """Whether a declared workflow's scope covers a suggestion's: a declared workflow with no
    scope covers everything; one with a scope covers a suggestion of the same scope or one
    inside it."""
    if declared.scope is None:
        return True
    if suggested.scope is None:
        return False
    outer, inner = declared.scope.strip("/").casefold(), suggested.scope.strip("/").casefold()
    return inner == outer or inner.startswith(outer + "/")


def workflows_in_force_for(
    declared: Iterable[Workflow], suggested: Iterable[Workflow], refused: object = ()
) -> tuple[Workflow, ...]:
    """The workflows a run applies: every declared one, then each suggestion no declared
    workflow displaces and no refusal names.

    A person's declared workflow displaces every suggestion its scope covers. One with no scope
    displaces them all, since a laboratory that has said how its work goes has answered what a
    suggestion asks. ``refused`` is what :func:`workflows_refused` returns, or identifiers alone.
    """
    held = tuple(declared)
    out = Refusals.of(refused)
    kept = [
        workflow
        for workflow in suggested
        if not any(_covers(each, workflow) for each in held) and not out.refuses(workflow)
    ]
    return (*held, *kept)


def write_workflows(
    directory: Path,
    *,
    declared: Sequence[Workflow],
    suggested: Sequence[Workflow],
    refused: object = (),
) -> None:
    """Say, in a run's directory, which workflows the run suggested, which it read as declared,
    and which of them it applied."""
    in_force = {w.identifier for w in workflows_in_force_for(declared, suggested, refused)}
    body = {
        "workflows": [
            {
                **workflow.as_payload(),
                "id": workflow.identifier,
                "in_force": workflow.identifier in in_force,
            }
            for workflow in (*declared, *suggested)
        ]
    }
    (directory / WORKFLOWS_FILE).write_text(
        json.dumps(body, sort_keys=True, indent=1) + "\n", encoding="utf-8"
    )


def read_workflows(directory: Path | None) -> list[dict[str, Any]]:
    """The workflows a run's directory states, each payload with its ``id`` and ``in_force``.

    The list is empty where the run states none, as for a run made before the workflow suggester
    existed or one made with the switch off.
    """
    if directory is None:
        return []
    try:
        loaded = json.loads((directory / WORKFLOWS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    held = loaded.get("workflows") if isinstance(loaded, dict) else None
    return [item for item in held if isinstance(item, dict)] if isinstance(held, list) else []


def carry_workflows(source: Path, destination: Path) -> None:
    """Copy the workflows statement of ``source`` into ``destination``, for a run that carries that
    run's claims forward (a dive). Nothing is copied where the source states none."""
    held = source / WORKFLOWS_FILE
    if held.is_file():
        (destination / WORKFLOWS_FILE).write_text(
            held.read_text(encoding="utf-8"), encoding="utf-8"
        )
