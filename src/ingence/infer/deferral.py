"""Find the folders the Rulebook defers beneath, and what lies under them, from a census.

Every rule defers beneath an installed program. A project revision's ``defer_beneath`` names the
folder rule whose raising makes a region a program's, and every rule whose subjects lie in that
region publishes what it says there as conflicting. A dive defers there too. Otherwise it would
spend part of its budget reading a program's own libraries, and nothing is concluded from those.
So the dive asks the same question of the same rule as the Rulebook does, and this module asks
it.

One declaration is read twice. Nothing here names a rule or a program. The regions are the
folders that the Rulebook's own deferral scopes raise. Each scope's predicate runs with its own
parameters. It reads only the census's names and kinds, which is all a layout reads, and the
programs and packages that the vocabulary declares. A project whose Rulebook defers beneath
nothing has no region, and a dive over it reads the same files as it would without deferral.

Nothing is opened and nothing is concluded. A region is not a claim. The dive only orders its
reading by the region, and the inference pass raises the same folders as findings, with their
reasons, for a person to read.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from ingence.core.ids import ScopeKind
from ingence.infer.evaluate import parameters_in_force
from ingence.infer.records import assemble_scope_records
from ingence.infer.subjects import EntryRecords
from ingence.rulebook.registry import (
    PREDICATES,
    PredicateNotEvaluableError,
    Raised,
    Reads,
    VocabularyRead,
    reads_of,
    vocabulary_of,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ingence.core.schema import Entry, RecordEvent
    from ingence.dictionary.programs import ProgramLayout
    from ingence.infer.subjects import ScopeRecords
    from ingence.rulebook.resolve import EffectiveRulebook

__all__ = ["DeferredRegions", "deferred_regions", "folders_said_programs"]

#: The level a deferral's scope raises, as the Rulebook refuses any other (``_defer_beneath``).
_FOLDER_LEVEL = "folder"


def folders_said_programs(events: Sequence[RecordEvent]) -> frozenset[str]:
    """Return the folders whose latest program answer says they are one.

    The answer to "Is this folder a program?" about software outside the catalogue, read the way
    the "not a program" answer is (``record.verdicts.folders_not_programs``), by any actor alike.
    """
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import latest_by_target
    from ingence.record.targets import PROGRAM_PREFIX

    prefix = f"{PROGRAM_PREFIX}:"
    return frozenset(
        target[len(prefix) :]
        for target, event in latest_by_target(events).items()
        if target.startswith(prefix) and event.kind is RecordEventKind.CONFIRM
    )


@dataclass(frozen=True, slots=True)
class DeferredRegions:
    """The folders a Rulebook's deferral scopes raised over a census, and what lies beneath them."""

    #: Each folder raised, by its entry, with the rule that raised it and what it raised it as.
    raised: Mapping[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    #: Every entry beneath a raised folder, at any depth. The folders themselves are not in it.
    beneath: frozenset[str] = frozenset()
    #: The deferral scopes read, by rule, in canonical order.
    scopes: tuple[str, ...] = ()
    #: The digest of the Rulebook the scopes were read from, which a run names beside the order.
    rulebook_digest: str = ""


def deferred_regions(
    entries: Iterable[Entry],
    book: EffectiveRulebook,
    *,
    layouts: tuple[ProgramLayout, ...] = (),
    not_programs: frozenset[str] = frozenset(),
    programs: frozenset[str] = frozenset(),
) -> DeferredRegions:
    """Evaluate the Rulebook's deferral scopes over a census's folders, and walk beneath them.

    ``layouts`` are the vocabulary's programs and packages, handed to a scope whose predicate
    declares it reads them, as the inference pass hands them. Only the predicate's verdict is read,
    never its abstention. A folder that is not a program needs no reason here, and explaining every
    folder of a large census would make the common path slow. ``not_programs`` are the folders a
    person took out of the program scope, read from the Record. ``programs`` are the folders a
    person put in it (see :func:`folders_said_programs`).
    """
    # Read the shipped book's deferral scopes and the project's own. The scope that every rule
    # defers beneath is in the shipped book.
    scopes = tuple(sorted({scope for _, scope, _ in (*book.shipped_deferred, *book.deferred)}))
    held = [entry for entry in entries if not entry.is_deleted]
    if not scopes or not held:
        return DeferredRegions(scopes=scopes, rulebook_digest=book.digest)
    rules = []
    for rule_id in scopes:
        rule = book.by_id(rule_id)
        if rule is None or rule.level != _FOLDER_LEVEL:
            continue
        declaration = PREDICATES.declaration(rule.predicate)
        if reads_of(declaration) is not Reads.SCOPE:
            continue
        rules.append(
            (
                rule,
                PREDICATES.get(rule.predicate),
                parameters_in_force(rule),
                VocabularyRead.PROGRAM_LAYOUTS in vocabulary_of(declaration),
            )
        )
    # A folder that a person said is not a program is not one for the dive or for inference.
    records = [
        EntryRecords(
            entry=entry,
            not_a_program=entry.entry_id in not_programs,
            a_program=entry.entry_id in programs,
        )
        for entry in held
    ]
    wanted = layouts if any(reads for *_, reads in rules) else ()
    raised: dict[str, tuple[str, tuple[str, ...]]] = {}
    for scope in assemble_scope_records(records, layouts=wanted):
        if scope.scope.kind is not ScopeKind.FOLDER or scope.scope.value is None:
            continue
        for rule, predicate, parameters, reads in rules:
            handed = scope if reads else _without_layouts(scope)
            try:
                verdict = predicate.evaluate(handed, parameters)
            except PredicateNotEvaluableError:
                continue
            if isinstance(verdict, Raised) and verdict.values:
                raised[scope.scope.value] = (rule.rule_id, tuple(verdict.values))
                break
    parents = {entry.entry_id: entry.parent_entry_id for entry in held}
    return DeferredRegions(
        raised=raised,
        beneath=_beneath(parents, frozenset(raised)),
        scopes=scopes,
        rulebook_digest=book.digest,
    )


def _without_layouts(scope: ScopeRecords) -> ScopeRecords:
    """The scope as a predicate that declared no vocabulary is handed it: without the layouts."""
    return replace(scope, layouts=())


def _beneath(parents: Mapping[str, str | None], raised: frozenset[str]) -> frozenset[str]:
    """Every entry with a raised folder among its ancestors, each folder's answer read once."""
    known: dict[str, bool] = {}

    def under(folder: str | None) -> bool:
        path: list[str] = []
        answer = False
        while folder is not None:
            if folder in known:
                answer = known[folder]
                break
            if folder in raised:
                answer = True
                break
            path.append(folder)
            folder = parents.get(folder)
        for visited in path:
            known[visited] = answer
        if folder is not None and folder in raised:
            known[folder] = True
        return answer

    return frozenset(entry for entry, parent in parents.items() if under(parent))
