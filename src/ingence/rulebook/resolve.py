"""Compose revisions into the Rulebook that one run actually used, and hash it.

Revisions compose and never replace. A revision may add a rule, supersede one, or raise a rule's
version with different parameters. Nothing is edited in place and nothing is deleted, because the
Rulebook is the record of what the engine believed and when, and a past run must replay exactly.
Superseding marks a rule inactive from a revision forward. The rule itself stays readable, and a run
recorded against an earlier revision still resolves to the rules it ran.

The effective Rulebook is a function of a revision number. Ask for revision 7 and you get the rules
in force at revision 7, whatever has happened since. That is what makes ``RunRecord.rulebook_hash``
meaningful and not decorative. Two runs that share a hash used the same rules at the same
parameters. For two runs that do not, you can tell exactly which rule differed.

A project's own revisions compose after the shipped ones, in a sequence of their own. The shipped
Rulebook grows with every release, so a project revision numbered 3 would otherwise collide with the
next shipped revision 3. A project revision holds deltas and never a copy. A parameter that it
overrides takes effect as a version of its own, so a claim's rule version names the settings it was
licensed at.

The hash covers what changes an answer and nothing else. A revision's summary prose does not enter
it, because editing a sentence that explains a rule must not invalidate every run that used the
rule. The rule's own statement template does enter it. The statement is what a person read when they
approved the rule, and a rule whose English changed while its behavior did not is still a different
thing to have agreed to.

Two constraints on a parameter sweep land here when the sweep is built, and neither is built. A
parameter sweep must report coverage against conflict as a curve and never as a single objective.
Failure rate is degenerate, because a rule that never fires has a perfect one. Any optimiser that
minimises failures converges on universal abstention and finds it first. Also, re-deciding from
stored evidence at the original parameters must reproduce the original run exactly. That is a test
to write before any curve is trusted. :func:`effective_rulebook` is the function that such a sweep
would call once per parameter setting. It is deterministic and free of the Index by construction, so
that it can be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json, digest_text

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from ingence.core.schema import Rule
    from ingence.rulebook.load import Deferral, ProjectChange, ProjectRevision, Revision

__all__ = ["EffectiveRulebook", "effective_rulebook", "version_key"]


@dataclass(frozen=True, slots=True)
class EffectiveRulebook:
    """The rules in force at one revision, and the digest that identifies them."""

    #: The revision this was resolved at. Every run record should carry it beside
    #: the hash, because a hash says two runs agree and a number says which
    #: Rulebook to open to find out what they agreed on.
    revision: int
    rules: tuple[Rule, ...]
    digest: str
    #: Rules that exist and are not in force here, with why. Kept rather than
    #: dropped, because "this rule was superseded at revision 5" and "no such rule"
    #: are different answers to a user asking why their rule did not fire, and a
    #: resolver that returns only the active set can give only the second.
    inactive: tuple[tuple[str, str], ...] = ()
    #: The last of a project's own revisions composed after the shipped one, or 0 where the project
    #: has none. The two are separate sequences, so the pair is what names a Rulebook.
    project_revision: int = 0
    #: Each rule that a project's deferral reached: the rule, the scope it now defers beneath, and
    #: the project revision that declared it, so that a rendering says so.
    deferred: tuple[tuple[str, str, int], ...] = ()
    #: Each rule that a shipped revision's deferral reached, the same way: the rule, the scope, and
    #: the shipped revision that declared it.
    shipped_deferred: tuple[tuple[str, str, int], ...] = ()

    def __iter__(self) -> Iterator[Rule]:
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    def by_id(self, rule_id: str) -> Rule | None:
        """Return the rule in force under this identifier, or None."""
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        return None


def effective_rulebook(
    revisions: Sequence[Revision],
    *,
    at: int | None = None,
    project: Sequence[ProjectRevision] = (),
    project_at: int | None = None,
) -> EffectiveRulebook:
    """Compose revisions into the rules in force at one revision.

    ``at`` defaults to the highest revision present, which is what a fresh run wants. Passing an
    earlier number replays a past run, and that is the reason superseding marks and does not delete.

    A later version of a rule replaces an earlier one. A later rule with a new identifier joins it.
    Within one identifier, the highest ``added_revision`` at or below ``at`` wins. Ties are broken
    by version, read as a number, so that two revisions carelessly numbered the same still compose
    deterministically and not by list order.

    A project's own revisions compose after the shipped ones, in their own sequence, up to
    ``project_at`` where one is given. Each holds deltas to the rules in force at that point. A
    delta that names a rule that is not in force, a parameter the rule does not declare, or a value
    the parameter refuses stops the Rulebook composing. The delta is not skipped, because that would
    leave a person believing that a change took effect.
    """
    ordered = sorted(revisions, key=lambda revision: revision.number)
    ceiling = ordered[-1].number if at is None and ordered else (at or 0)

    in_force: dict[str, Rule] = {}
    inactive: dict[str, str] = {}
    disabled_at: dict[str, str] = {}
    shipped_deferrals: list[tuple[int, Deferral]] = []
    for revision in ordered:
        if revision.number > ceiling:
            continue
        for rule_id in revision.supersedes:
            in_force.pop(rule_id, None)
            inactive[rule_id] = f"superseded at revision {revision.number}"
        # A shipped revision's changes are read as a project's are, before the rules the revision
        # declares, because a change names a rule that an earlier revision declared.
        _apply_changes(
            revision.changes,
            where=f"revision {revision.number}",
            mark=f"{_SHIPPED_MARK}{revision.number}",
            shipped=True,
            in_force=in_force,
            inactive=inactive,
            disabled_at=disabled_at,
        )
        shipped_deferrals.extend((revision.number, deferral) for deferral in revision.deferrals)
        for rule in revision.rules:
            if rule.added_revision > ceiling:
                continue
            if rule.superseded_revision is not None and rule.superseded_revision <= ceiling:
                in_force.pop(rule.rule_id, None)
                inactive[rule.rule_id] = f"superseded at revision {rule.superseded_revision}"
                continue
            standing = in_force.get(rule.rule_id)
            if standing is None or _supersedes(rule, standing):
                in_force[rule.rule_id] = rule
                inactive.pop(rule.rule_id, None)

    declared = {rule.rule_id for revision in ordered for rule in revision.rules}
    project_ceiling = 0
    applied: list[ProjectRevision] = []
    for delta in sorted(project, key=lambda delta: delta.number):
        if project_at is not None and delta.number > project_at:
            continue
        _apply_project_revision(
            delta,
            in_force=in_force,
            inactive=inactive,
            declared=declared,
            disabled_at=disabled_at,
        )
        applied.append(delta)
        project_ceiling = delta.number

    # A deferral reaches every rule in force once the deltas are composed. So a rule that a later
    # project revision adds defers too, which is the point of declaring the deferral once. A shipped
    # revision's deferral reaches a project's own rules the same way.
    reached_shipped: list[tuple[str, str, int]] = []
    for ordinal, (number, deferral) in enumerate(shipped_deferrals, start=1):
        reached_shipped.extend(
            _defer_beneath(
                deferral,
                number=number,
                in_force=in_force,
                declared=declared,
                where=f"revision {number}'s deferral beneath {deferral.scope!r}",
                # The first keeps +d1, the mark its rules carried as a project's revision 1; a
                # later one takes a letter of its own, so "+d1" names one deferral.
                mark=f"{_DEFERRAL_MARK}1" if ordinal == 1 else f"{_LATER_SHIPPED_MARK}{ordinal}",
                shipped=True,
            )
        )
    deferred: list[tuple[str, str, int]] = []
    for delta in applied:
        for deferral in delta.deferrals:
            deferred.extend(
                _defer_beneath(
                    deferral,
                    number=delta.number,
                    in_force=in_force,
                    declared=declared,
                    where=f"project revision {delta.number}'s deferral beneath {deferral.scope!r}",
                    mark=f"{_DEFERRAL_MARK}{delta.number}",
                )
            )

    # A disabled rule is in the Rulebook and out of force. Recorded as inactive
    # with its reason rather than silently omitted, because "switched off" and
    # "never existed" are answers to different questions and a user who switched
    # a rule off is exactly the person who will ask.
    for rule_id, rule in sorted(in_force.items()):
        if not rule.enabled:
            inactive[rule_id] = (
                f"disabled at {disabled_at[rule_id]}" if rule_id in disabled_at else "disabled"
            )
    active = tuple(rule for _, rule in sorted(in_force.items()) if rule.enabled)

    # A defeater that no revision declares is refused. One that exists and is not in force is not
    # refused. The first is a typo, and a claim that names it could never be retracted by anything.
    # The second is a person switching a defeater off, which must not stop the Rulebook composing.
    # The rule it defeated stops licensing claims and says why, and the claim pass reports that.
    for rule in active:
        unknown = sorted(set(rule.defeated_by) - declared)
        if unknown:
            from ingence.rulebook.load import RuleDeclarationError

            message = (
                f"rule {rule.rule_id!r} names defeaters no revision declares: "
                f"{', '.join(unknown)}; a claim naming them could never be retracted"
            )
            raise RuleDeclarationError(message)

    _refuse_an_index_that_groups_nothing(active, ordered, in_force, declared)

    return EffectiveRulebook(
        revision=ceiling,
        rules=active,
        digest=rulebook_digest(active),
        inactive=tuple(sorted(inactive.items())),
        project_revision=project_ceiling,
        deferred=tuple(sorted(deferred)),
        shipped_deferred=tuple(sorted(reached_shipped)),
    )


#: The effects a deferral reaches: every rule that concludes, groups, proposes or raises. The
#: scan layer's rules run in the census and a constraint licenses nothing, so neither has
#: anything to defer.
_DEFERRING_EFFECTS = frozenset({"classifies", "groups", "proposes", "flags"})

#: What separates a rule's version from the deferral that reached it. A project revision's deferral
#: is numbered by the revision (``+d2``). The shipped book's first deferral marks ``+d1``, the mark
#: that every rule it reaches carried while it was a project's revision 1. So a rule that the fold
#: (see :mod:`ingence.rulebook.retired`) leaves unchanged keeps its version, and the claims,
#: candidates and findings it concluded keep their identifiers (``core/ids.py``). A decision made
#: about one before the fold still applies to it.
_DEFERRAL_MARK = "+d"

#: What separates a rule's version from the book's second deferral onward: ``2+s2``.
_LATER_SHIPPED_MARK = "+s"

#: What separates a rule's version from the shipped revision that overrode its parameters without
#: declaring it again: ``2+r5``. It has a letter of its own, because the shipped and the project
#: sequences never share a number.
_SHIPPED_MARK = "+r"


def _defer_beneath(
    deferral: Deferral,
    *,
    number: int,
    in_force: dict[str, Rule],
    declared: set[str],
    where: str,
    mark: str,
    shipped: bool = False,
) -> list[tuple[str, str, int]]:
    """Make every rule a deferral reaches name its scope as a defeater, as a version of its own.

    A deferral is refused and not skipped where the scope is not a folder rule that raises, or where
    ``except`` names a rule that no revision declares. A typo there would exempt nothing, or defer
    what a person meant to keep, and nobody would see it happen. A shipped deferral whose scope a
    project took out of force reaches nothing. That is the person's choice, and it must not stop
    their Rulebook loading.
    """
    from ingence.core.schema import RuleEffect
    from ingence.rulebook.load import RuleDeclarationError

    scope = in_force.get(deferral.scope)
    if shipped and scope is None and deferral.scope in declared:
        return []
    if scope is None or scope.effect is not RuleEffect.FLAGS or scope.level != "folder":
        message = (
            f"{where}: a deferral's scope is a rule in force that raises a folder, and "
            f"{deferral.scope!r} is not one"
        )
        raise RuleDeclarationError(message)
    unknown = sorted(set(deferral.exempt) - declared)
    if unknown:
        message = f"{where}: 'except' names {', '.join(unknown)}, which no revision declares"
        raise RuleDeclarationError(message)
    reached: list[tuple[str, str, int]] = []
    for rule_id, rule in sorted(in_force.items()):
        if (
            rule_id == deferral.scope
            or rule_id in deferral.exempt
            or rule.effect.value not in _DEFERRING_EFFECTS
            or deferral.scope in rule.defeated_by
        ):
            continue
        # A deferral never makes a rule publish what it did not. A defeasible rule that names no
        # defeater publishes nothing. That is how a rule that serves only as another's defeater
        # (``later_stage_from_name``) stays one. If it were given the scope as its first defeater,
        # it would start publishing claims. A flag raises either way, so a deferral reaches it. A
        # rule that publishes nothing has nothing to defer.
        if rule.effect is not RuleEffect.FLAGS and not rule.defeated_by:
            continue
        in_force[rule_id] = rule.model_copy(
            update={
                "defeated_by": (*rule.defeated_by, deferral.scope),
                "version": f"{rule.version}{mark}",
            }
        )
        reached.append((rule_id, deferral.scope, number))
    return reached


def _refuse_an_index_that_groups_nothing(
    active: Sequence[Rule],
    revisions: Sequence[Revision],
    in_force: Mapping[str, Rule],
    declared: set[str],
) -> None:
    """Refuse a proposing rule whose index names no grouping rule that a revision declares.

    The check is made where a defeater is, and for the same two reasons. An index that no revision
    declares is a typo, and a rule drawing within it could never be handed a pair. An index that
    names a rule that groups nothing names classes that cannot exist. A grouping rule that is
    declared and not in force is neither, and it composes. The proposing rule then draws nothing,
    and the inference pass says why.
    """
    from ingence.core.schema import RuleEffect
    from ingence.rulebook.load import RuleDeclarationError
    from ingence.rulebook.registry import BUILT_IN_INDEXES

    effects = {rule.rule_id: rule.effect for revision in revisions for rule in revision.rules}
    effects.update({rule.rule_id: rule.effect for rule in in_force.values()})
    for rule in active:
        index = rule.candidates_within
        if index is None or index in BUILT_IN_INDEXES:
            continue
        if index not in declared:
            message = (
                f"rule {rule.rule_id!r} draws candidates within {index!r}, which no revision "
                f"declares; a rule drawing within it could never be handed a pair"
            )
            raise RuleDeclarationError(message)
        if index in effects and effects[index] is not RuleEffect.GROUPS:
            message = (
                f"rule {rule.rule_id!r} draws candidates within {index!r}, whose effect is "
                f"{effects[index].value}; pairs are drawn within a grouping rule's classes or "
                f"within one folder"
            )
            raise RuleDeclarationError(message)


#: What separates a rule's shipped version from the project revision that tuned it.
_PROJECT_MARK = "+p"


def _apply_project_revision(
    delta: ProjectRevision,
    *,
    in_force: dict[str, Rule],
    inactive: dict[str, str],
    declared: set[str],
    disabled_at: dict[str, str],
) -> None:
    """Apply one project revision's deltas to the rules in force, in the order it lists them.

    A project revision holds deltas only: switch a rule on or off, override its parameters,
    supersede it, or add a rule of a new name. A project rule may not reuse a name any revision
    declares, because a second declaration under one name would be an edit in place by another
    route; the change a project wants is an override, or a supersession and a rule of its own.
    """
    from ingence.rulebook.load import RuleDeclarationError

    where = f"project revision {delta.number}"
    _apply_changes(
        delta.changes,
        where=where,
        mark=f"{_PROJECT_MARK}{delta.number}",
        shipped=False,
        in_force=in_force,
        inactive=inactive,
        disabled_at=disabled_at,
    )
    for rule in delta.rules:
        if rule.rule_id in declared:
            message = (
                f"{where}: declares {rule.rule_id!r}, which a revision already declares; "
                f"override its parameters, or supersede it and declare a rule of a new name"
            )
            raise RuleDeclarationError(message)
        declared.add(rule.rule_id)
        in_force[rule.rule_id] = rule


def _apply_changes(
    changes: Sequence[ProjectChange],
    *,
    where: str,
    mark: str,
    shipped: bool,
    in_force: dict[str, Rule],
    inactive: dict[str, str],
    disabled_at: dict[str, str],
) -> None:
    """Apply one revision's changes to the rules in force, in the order it lists them.

    A shipped revision and a project's revision use the same four verbs. Only a project's change is
    refused on a provisional rule, which stays in force as shipped until a person confirms it. Each
    change names its own sequence in what it records.
    """
    from ingence.core.schema import RuleStatus
    from ingence.rulebook.load import ChangeVerb, RuleDeclarationError

    for change in changes:
        standing = in_force.get(change.rule_id)
        if standing is None:
            reason = inactive.get(change.rule_id)
            # Said in words: a person reads this refusal, so it names no book numbers.
            from ingence.rulebook.render import plain_reason

            state = "not declared by any revision"
            if reason:
                state = f"not in force: {plain_reason(reason)}"
            message = f"{where}: {change.verb.value} names {change.rule_id!r}, which is {state}"
            raise RuleDeclarationError(message)
        # A provisional rule stays in force as shipped until a person confirms it. The scan layer's
        # four rules are provisional. The containment forest and the embedding edges they draw are
        # what the publication checks INV-1 and INV-8 verify (``core/invariants.py``).
        if (
            not shipped
            and standing.status is RuleStatus.PROVISIONAL
            and change.verb is not ChangeVerb.ENABLE
        ):
            message = (
                f"{where}: {change.verb.value} names {change.rule_id!r}, which is provisional; "
                f"a provisional rule stays in force as shipped until a person confirms it"
            )
            raise RuleDeclarationError(message)
        if change.verb is ChangeVerb.SUPERSEDE:
            del in_force[change.rule_id]
            disabled_at.pop(change.rule_id, None)
            inactive[change.rule_id] = f"superseded at {where}"
        elif change.verb is ChangeVerb.DISABLE:
            in_force[change.rule_id] = standing.model_copy(update={"enabled": False})
            disabled_at[change.rule_id] = where
        elif change.verb is ChangeVerb.ENABLE:
            in_force[change.rule_id] = standing.model_copy(update={"enabled": True})
            disabled_at.pop(change.rule_id, None)
        else:
            in_force[change.rule_id] = _overridden(
                standing, change.parameters, mark=mark, where=where
            )


def _overridden(rule: Rule, values: Mapping[str, object], *, mark: str, where: str) -> Rule:
    """Return a rule at the values a project set, as a version of its own.

    The version says which settings a claim was licensed at. A rule that a project tunes at project
    revision 3 composes as ``1+p3``: the version the shipped Rulebook gave it, and the project
    revision whose composition it reflects. Tuned again at revision 5, it becomes ``1+p5`` and
    carries both changes. Without that, two claims at version ``1`` under different settings could
    be told apart only by a run's digest, and a claim read on its own would name settings it was not
    licensed at.

    The values replace the parameters' stored defaults, because the stored default is what
    :func:`~ingence.rulebook.render.value_in_force` reads. So a derived version's default is the
    value in force for it.
    """
    from ingence.rulebook.load import RuleDeclarationError, refuse_a_value_its_parameter_refuses

    specifications = {specification.name: specification for specification in rule.parameters}
    unknown = sorted(set(values) - set(specifications))
    if unknown:
        message = (
            f"{where}: override of {rule.rule_id!r} sets {', '.join(unknown)}, "
            f"which the rule does not declare"
        )
        raise RuleDeclarationError(message)
    for name in sorted(values):
        refuse_a_value_its_parameter_refuses(
            specifications[name], values[name], where=f"{where}: override of {rule.rule_id!r}"
        )
    parameters = tuple(
        specification.model_copy(update={"default": canonical_json(values[specification.name])})
        if specification.name in values
        else specification
        for specification in rule.parameters
    )
    # The mark's letter, without its number: a later override of the same sequence replaces an
    # earlier one's mark, and carries both changes.
    letter = mark.rstrip("0123456789")
    base = rule.version.split(letter, 1)[0]
    return rule.model_copy(update={"parameters": parameters, "version": f"{base}{mark}"})


def rulebook_digest(rules: Iterable[Rule]) -> str:
    """Return the digest identifying one set of rules at their parameters.

    The rules are put in canonical order before hashing, per the standing convention, so the digest
    is a fact about the rules and not about the order that a directory listing happened to return.

    What is in the digest, and why each part:

    - The identifier and version say which rule.
    - The predicate and its effect say what runs and in which phase.
    - The predicate's version says which implementation of it. A predicate bumps that when its
      verdict could change for unchanged input, which is exactly the event that a run needs to
      re-decide what it affected.
    - The parameters say at what settings, which is the whole point of a sweep being able to compare
      two digests.
    - The tier says how strong a claim the rule may emit.
    - The composition says what else had to hold.
    - A proposing rule's index says which pairs it can ever be handed.
    - The statement is in because a person approved a sentence. A rule whose sentence changed is a
      different thing to have agreed to, even where its behavior did not change.

    The relation is in, all three parts of it. A rule retargeted from ``variant_of`` to
    ``replicate_of`` keeps its predicate, its parameters and often its sentence. It asserts
    something materially different about every pair it touches. One says that a design was
    deliberately changed, and the other says that the same design was performed again. If two runs
    whose digests agreed meant that, the hash would fail at the one job it has. The respect is
    redundant with the specialization where one is named, and it is hashed anyway, because it is not
    redundant where none is.

    What is out of the digest: the rationale and every parameter description, which are prose for a
    reader. Editing an explanation must not invalidate every run that used the rule it explains. The
    status is out as well, because confirming a rule changes nothing that it concludes.
    """
    payload = [
        {
            "rule_id": rule.rule_id,
            "version": rule.version,
            "predicate": rule.predicate,
            "predicate_version": rule.predicate_version,
            "effect": rule.effect.value,
            "primitive": None if rule.primitive is None else rule.primitive.value,
            "specialization": rule.specialization,
            "respect": rule.respect,
            "tier": rule.tier.value,
            "defeated_by": list(rule.defeated_by),
            "candidates_within": rule.candidates_within,
            "level": rule.level,
            "scope": rule.scope.token,
            "statement": rule.statement,
            "parameters": [
                {
                    "name": specification.name,
                    "type": specification.type.value,
                    "default": specification.default,
                    "choices": list(specification.choices),
                    "minimum": specification.minimum,
                    "maximum": specification.maximum,
                }
                for specification in rule.parameters
            ],
            "composition": (
                None
                if rule.composition is None
                else {
                    "all_of": list(rule.composition.all_of),
                    "any_of": list(rule.composition.any_of),
                    "none_of": list(rule.composition.none_of),
                }
            ),
        }
        for rule in sorted(rules, key=lambda rule: (rule.rule_id, rule.version))
    ]
    return digest_text(canonical_json(payload))


def _supersedes(candidate: Rule, standing: Rule) -> bool:
    """Whether one version of a rule displaces another already in force.

    The comparison is by revision first, then by version, read as a number (:func:`version_key`).
    Read as text, ``"10"`` would sort before ``"9"``. The tie-break exists so that two revisions
    carelessly given the same number still compose to one answer, and not to whichever the list
    happened to hold first. That would make the effective Rulebook depend on file order and break
    the rule that a run is reproducible from its Rulebook.
    """
    if candidate.added_revision != standing.added_revision:
        return candidate.added_revision > standing.added_revision
    return version_key(candidate.version) > version_key(standing.version)


#: A rule's version: its number (dotted parts allowed), then any marks a revision added, each a
#: letter and the number of the revision that added it (``3+d1``, ``1+p2``, ``2+r4``).
_VERSION = re.compile(r"^(?P<number>[0-9]+(?:\.[0-9]+)*)(?P<marks>(?:\+[a-z][0-9]+)*)$")
_MARK = re.compile(r"\+([a-z])([0-9]+)")


def version_key(version: str) -> tuple[object, ...]:
    """The order of a rule's versions, as numbers, so that ``10`` follows ``9``.

    A version that the engine never writes (a word, a malformed mark) sorts after every numbered
    one, by its text. So the order is still total and still a fact about the versions alone.
    """
    matched = _VERSION.match(version.strip())
    if matched is None:
        return (1, (), (), version)
    number = tuple(int(part) for part in matched["number"].split("."))
    marks = tuple((letter, int(digits)) for letter, digits in _MARK.findall(matched["marks"]))
    return (0, number, marks, "")
