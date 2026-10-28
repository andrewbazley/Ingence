"""A flagging predicate: an unbound word standing where words of one kind stand.

The predicate is a rule and not a statistical model. Siblings are written from a template, so a
place in the template holds one kind of thing. Three reagents in the second place of a folder's
names make the fourth word there a question about a reagent, asked once with the three shown.
Clustering is a statistic with a threshold nobody chose. This rule is a sentence that a person can
read, tune and switch off. :mod:`ingence.textword.slots` does the alignment and is shared with the
page's function, so the rule and the page cannot disagree about a folder.

The rule raises and concludes nothing. It makes a finding. The word stays unbound until a person
answers, and the answer is the term sheet's (``term:<word>``). The finding's reason names the
place (by an example name), the bound words and how many of them share the kind, in plain words:
"kelt stands where adp, atp and tcep stand (the second part of 8 names written alike, such as
SubX_adp_r1.txt), and all 3 words known there are reagents".

The rule asks one question per word, however many folders raise it. One answer satisfies all
related questions about that word. The rule raises each unbound word as its own value, and the
Review queue keys a question by rule and raised value. So a word raised in six folders is one
question with six findings beneath it, and one answer covers every finding of it.

The rule reads a folder's names by position. The pass loads the positions only where a rule in
force declares that it needs them (``reads_name_slots``), because a name layout is one more table
to read and memory is the figure to watch. Without the positions, this predicate abstains with
``unexamined`` and says why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.schema import RuleEffect
from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.registry import Abstention, Raised, Reads, declare
from ingence.textword.slots import (
    ASKED_SHOWN,
    BOUND,
    DEFAULTS,
    UNBOUND,
    WORDS_SHOWN,
    AlignedName,
    PlaceReading,
    Slot,
    known_there,
    listed,
    paradigms,
    places_of_a_kind,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.infer.subjects import EntryRecords, ScopeRecords
    from ingence.rulebook.registry import Predicate

__all__ = ["UnboundWordWhereAKindStands", "aligned_names", "reason_for"]


def _parameter(parameters: Mapping[str, object], name: str) -> str:
    """A parameter in force, or its one default (:data:`~ingence.textword.slots.DEFAULTS`)."""
    return str(parameters.get(name, DEFAULTS[name]))


def aligned_names(scope: ScopeRecords) -> list[AlignedName]:
    """A folder's names as aligned slots, from the records the pass handed the rule.

    Only names whose slots were loaded: an entry whose layout is absent is left out rather than
    guessed at. A slot naming a component the entry does not carry reads as nothing.
    """
    names: list[AlignedName] = []
    for records in scope.entries:
        if not records.slots:
            continue
        names.append(
            AlignedName(
                entry_id=records.entry.entry_id,
                folder_id=scope.token,
                slots=tuple(_slot(records, identifier) for identifier in records.slots),
                name=records.entry.name,
            )
        )
    return names


def reason_for(
    reading: PlaceReading, *, asked_shown: int = ASKED_SHOWN, words_shown: int = WORDS_SHOWN
) -> str:
    """One place's reason, in words a person reads without the code.

    "kelt stands where adp, atp and tcep stand (the second part of 8 names written alike, such as
    SubX_adp_r1.txt), and all 3 words known there are reagents". The verb agrees with the words
    asked about, the place is shown by an example name, and the kind is said as a person says it.
    """
    asked = reading.unbound
    stands = "stands" if len(asked) == 1 else "stand"
    beside = "stands" if len(reading.alongside) == 1 else "stand"
    return (
        f"{listed(asked, shown=asked_shown)} {stands} where "
        f"{listed(reading.alongside, shown=words_shown)} {beside} ({reading.where()}), and "
        f"{known_there(reading.agreeing, reading.bound, reading.kind)}"
    )


def _slot(records: EntryRecords, component_id: str) -> Slot:
    component = next((c for c in records.components if c.component_id == component_id), None)
    if component is None:
        return Slot(token="", component_type="?")
    binding = next((b for b in records.bindings if b.component_id == component_id), None)
    state: str | None = None
    kind: str | None = None
    if binding is not None:
        if binding.resolution_status is ResolutionStatus.RESOLVED and binding.theme_kind:
            state, kind = BOUND, binding.theme_kind
        elif binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE:
            state = UNBOUND
    return Slot(
        token=component.normalized_value,
        component_type=component.component_type,
        state=state,
        kind=kind,
        records=(component_id, *(() if binding is None else (binding.binding_id,))),
    )


class UnboundWordWhereAKindStands:
    """Raises an unbound word standing in a place whose bound words mostly share one axis."""

    name = "unbound_word_where_a_kind_stands"
    #: 2: the reason says the place in plain words, by an example name.
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> Raised | None:
        scope: ScopeRecords = subject  # type: ignore[assignment]
        found = paradigms(
            aligned_names(scope), minimum_names=int(_parameter(parameters, "minimum_names"))
        )
        readings = places_of_a_kind(
            found,
            minimum_bound=int(_parameter(parameters, "minimum_bound")),
            agreement=float(_parameter(parameters, "agreement")),
        )
        if not readings:
            return None
        values: set[str] = set()
        inputs: set[str] = set()
        reasons: list[str] = []
        for reading in readings:
            values.update(reading.unbound)
            for word in (*reading.unbound, *reading.alongside):
                inputs.update(reading.records.get(word, ()))
            reasons.append(reason_for(reading))
        # A finding rests on the scope's own records and never on another's.
        owned = scope.owns()
        return Raised(
            values=tuple(sorted(values)),
            inputs=tuple(sorted(inputs & owned)),
            reason="; ".join(sorted(set(reasons))),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        scope: ScopeRecords = subject  # type: ignore[assignment]
        if not any(records.slots for records in scope.entries):
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason="the names' places were not loaded for this run, so no place could be read",
            )
        return Abstention(
            reason="no place in this folder's names holds a word nothing binds beside words "
            "bound to one kind"
        )


@declare(
    "unbound_word_where_a_kind_stands",
    version="2",
    summary="a word nothing binds, standing where words bound to one kind stand in sibling names",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=("minimum_bound", "agreement", "minimum_names"),
    reads_name_slots=True,
)
def _build_unbound_word_where_a_kind_stands() -> Predicate:
    return UnboundWordWhereAKindStands()
