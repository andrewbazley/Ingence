"""Read a revision file into rules, and refuse the ones that are not rules.

The files are YAML, for the same reason the Dictionary is YAML. These files are the product. A user
who disagrees with a conclusion should be able to open the rule that licensed it, read the sentence,
change a parameter and re-run. That is worth more than the milliseconds that a binary format would
save when loading a few dozen rules once per run.

Everything checkable is checked here, at load, and not at evaluation. A rule is broken if its
predicate was deleted, its effect disagrees with its predicate, its sentence mentions a parameter
that does not exist, its predicate never reads a parameter it declares, or its default falls outside
its own bounds. A broken rule that is discovered halfway through a run has already produced a
partial graph. Loading is the one moment where refusing is free.

A rule that asserts a relationship says which one. A proposing or grouping rule names one of the
eight primitives. It may name a specialization from the catalog, which is what separates
``replicate_of`` from ``same_condition_as``. Both are ``equivalent`` in respect
``experimental_condition``, and one of them claims a separate occasion. The respect comes from the
specialization where one is named. It is stated directly where none covers the case, so there is
never a second copy to drift. A grouping rule's primitive must be symmetric and transitive. The
loader reads that off the declared signature and not from a list, because a grouping is a quotient
and ``concerns`` is symmetric and never transitive.

A project's own revisions are read here too, from its ``rulebook`` directory. They are numbered in a
sequence of their own, hold deltas and not copies, and are refused at load where a change is
malformed. Whether a change names a rule in force, or sets a value its parameter accepts, is known
only once revisions compose. :mod:`ingence.rulebook.resolve` refuses those.

Four design constraints govern how rules are written and tuned. Two of them are enforced here, and
they could not be enforced anywhere else.

No rule with ``effect: proposes`` may take a window or burst identifier as a scoring input. The
loader checks this against the predicate's declared support families. The constraint exists because
temporal overlap is not evidence of relatedness: two unrelated projects running in parallel overlap
perfectly. A burst may bound candidate generation and may be displayed. It may never contribute
positive support.

Rule form stays human-authored, permanently. This module is the only place in ``src/`` that
constructs a :class:`~ingence.core.schema.Rule`, and a test asserts it. That is the structural form
of the constraint. The proposal machinery may emit parameter values and Dictionary entries, and it
has no code path that writes a ``predicate`` field, because there is exactly one such path and it
reads a file that a person wrote. If a rule's form were machine-authored, a conclusion would become
traceable to a rule that nobody wrote, which is not attributable in any sense a reader would accept.

The other two constrain a parameter sweep that does not exist yet. See
:mod:`ingence.rulebook.resolve` for where they will land.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from ingence.core.ids import PROJECT_SCOPE, canonical_json
from ingence.core.relations import (
    PRIMITIVE_PROPERTIES,
    Primitive,
    Specialization,
    SpecializationRegistry,
)
from ingence.core.schema import (
    ParameterSpec,
    ParameterType,
    Rule,
    RuleComposition,
    RuleEffect,
    RuleStatus,
)
from ingence.core.tiers import InferenceTier
from ingence.rulebook.registry import (
    PREDICATES,
    READS_BY_EFFECT,
    TEMPORAL_FAMILIES,
    effect_of,
    families_of,
    parameters_of,
    reads_of,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.core.ids import Scope

__all__ = [
    "BUILTIN_DIRECTORY",
    "PROJECT_REVISION_KEY",
    "ChangeVerb",
    "Deferral",
    "ProjectChange",
    "ProjectRevision",
    "Revision",
    "RuleDeclarationError",
    "load_project_revision",
    "load_project_revisions",
    "load_revision",
    "load_revisions",
    "project_revision_from",
    "refuse_a_value_its_parameter_refuses",
    "revision_from",
]

#: Where the shipped revisions live. There is one file per revision, named so that the order on disk
#: is the order they compose in. Revision 4 is declared in Python
#: (:mod:`ingence.rulebook.revision_4`) and loaded with them.
BUILTIN_DIRECTORY = Path(__file__).parent / "builtin"

#: A ``{slot}`` in a rule's English sentence.
_SLOT = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

#: The shipped specialization table, read to resolve a rule's declared
#: specialization into its parent primitive and its respect.
#:
#: One registry per process rather than one per file, because registering the
#: catalog validates every entry in it and doing that once per revision file
#: would make loading cost grow with the number of revisions for no answer that
#: changes. Nothing here mutates it; a project that adds a specialization will
#: pass its own registry in rather than reaching for this one.
_SPECIALIZATIONS = SpecializationRegistry()


class RuleDeclarationError(ValueError):
    """A rule file declared something that is not a rule.

    A distinct type because the remedy is always the same and always a person's:
    edit the file. It is never an engine failure and never something a run should
    try to work around, which is why loading refuses rather than skipping the
    offending rule. A Rulebook missing one rule silently is a Rulebook that
    produces different answers than the one on disk says it will.
    """


@dataclass(frozen=True, slots=True)
class Revision:
    """One append-only revision of the Rulebook.

    Revisions compose rather than replace: a later one may add a rule, supersede
    one, or change a parameter, and the earlier one stays readable so any past run
    replays. That is why :class:`~ingence.core.schema.Rule` carries
    ``added_revision`` and ``superseded_revision`` rather than a file simply being
    edited in place.
    """

    number: int
    summary: str
    rules: tuple[Rule, ...]
    #: Rule identifiers this revision retires, as of this revision.
    supersedes: tuple[str, ...] = ()
    #: What the revision changes in rules that an earlier revision declared: an override, a
    #: supersession, a rule switched on or off. The shipped loader reads them exactly as it reads a
    #: project revision's.
    changes: tuple[ProjectChange, ...] = ()
    #: The scopes beneath which every rule in force defers, from this revision on.
    deferrals: tuple[Deferral, ...] = ()


def load_revision(path: Path, *, scope: Scope = PROJECT_SCOPE) -> Revision:
    """Read and validate one revision file."""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return revision_from(document, name=path.name, scope=scope)


def revision_from(document: object, *, name: str, scope: Scope = PROJECT_SCOPE) -> Revision:
    """Validate one shipped revision, from a file or from a declaration held in Python.

    ``name`` is what a refusal names: the file, or the module declaring it.
    """
    if not isinstance(document, dict):
        message = f"{name}: a revision file must be a mapping"
        raise RuleDeclarationError(message)

    number = document.get("revision")
    if not isinstance(number, int):
        message = f"{name}: a revision must carry an integer 'revision' number"
        raise RuleDeclarationError(message)

    declarations = document.get("rules") or []
    declared_changes = document.get("changes") or []
    if not isinstance(declarations, list) or not isinstance(declared_changes, list):
        message = f"{name}: 'rules' and 'changes' must each be a list"
        raise RuleDeclarationError(message)

    where = f"{name}: revision {number}"
    path = Path(name)
    rules = tuple(
        _rule_from(declaration, path=path, revision=number, scope=scope)
        for declaration in declarations
    )
    _refuse_duplicate_identifiers(rules, path=path)
    changes = tuple(_change_from(item, where=where) for item in declared_changes)
    changed = [change.rule_id for change in changes]
    repeated = sorted(
        {rule_id for rule_id in changed if changed.count(rule_id) > 1}
        | (set(changed) & {rule.rule_id for rule in rules})
    )
    if repeated:
        message = (
            f"{where}: names {', '.join(repeated)} more than once; one revision says one "
            f"thing about a rule"
        )
        raise RuleDeclarationError(message)
    return Revision(
        number=number,
        summary=str(document.get("summary", "")),
        rules=rules,
        supersedes=tuple(str(item) for item in document.get("supersedes", ()) or ()),
        changes=changes,
        deferrals=_deferrals_from(document.get("defer_beneath"), where=where),
    )


def load_revisions(
    directory: Path = BUILTIN_DIRECTORY, *, scope: Scope = PROJECT_SCOPE
) -> tuple[Revision, ...]:
    """Load every revision under a directory, in revision order.

    Sorted by the number the file declares rather than by filename, because a
    filename is a convention and the number is the thing composition depends on.
    Two revisions sharing a number is refused: composition order would then depend
    on the order the filesystem handed back names, which would break reproducibility
    and not merely inconvenience a reader.
    """
    revisions = [load_revision(path, scope=scope) for path in sorted(directory.glob("*.yaml"))]
    if directory.resolve() == BUILTIN_DIRECTORY.resolve():
        revisions.extend(_declared_in_python(scope=scope))
    seen: dict[int, str] = {}
    for revision in revisions:
        if revision.number in seen:
            message = (
                f"revision {revision.number} is declared twice; composition order would "
                f"then depend on the order the filesystem returned names"
            )
            raise RuleDeclarationError(message)
        seen[revision.number] = revision.summary
    return tuple(sorted(revisions, key=lambda revision: revision.number))


def _declared_in_python(*, scope: Scope) -> list[Revision]:
    """The shipped revisions declared in Python rather than YAML, read the same way.

    Revision 4 is shipped with the engine. Its declaration stays one dict in its module, where
    its rules were written and tested, and composes after revisions 0 to 3 like any other.
    """
    from ingence.rulebook import revision_4, revision_5

    return [
        revision_from(revision_4.REVISION, name=Path(revision_4.__file__).name, scope=scope),
        # Revision 5 holds the 0.4.0 rules. It is a package assembled from one module per subject
        # (reading, file types, names, workflows, areas and readouts), and it composes after
        # revision 4.
        revision_from(revision_5.REVISION, name="revision_5", scope=scope),
    ]


# ---------------------------------------------------------------------------
# A project's own revisions.
# ---------------------------------------------------------------------------

#: What a project revision numbers itself with. Deliberately not ``revision``, which
#: numbers the shipped Rulebook, so that a file cannot be read into the wrong sequence.
PROJECT_REVISION_KEY = "project_revision"


class ChangeVerb(StrEnum):
    """What a project revision may do to a rule already in force.

    These are the deltas a project revision may make, less adding a rule, which is not a change
    to an existing rule. A rule that a project adds is declared whole, under a name of its own.
    """

    ENABLE = "enable"
    DISABLE = "disable"
    SUPERSEDE = "supersede"
    OVERRIDE = "override"


@dataclass(frozen=True, slots=True)
class ProjectChange:
    """One delta a project revision makes to a rule already in force."""

    verb: ChangeVerb
    rule_id: str
    #: The values an override sets, by parameter name. Empty for every other verb.
    parameters: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectRevision:
    """One append-only revision of a project's own rules.

    Revisions are numbered in a sequence of their own. The shipped Rulebook grows with every
    release, so a project revision numbered 3 would collide with the next shipped revision 3. A
    project revision composes after whichever shipped revision is in force, and the pair of numbers
    names the Rulebook that a run used.

    A project revision holds deltas and never a copy. The shipped files are never edited, and
    nothing here restates them. A project switches a rule on or off, overrides its parameters,
    supersedes it, or adds a rule of a name of its own.
    """

    number: int
    summary: str
    changes: tuple[ProjectChange, ...] = ()
    rules: tuple[Rule, ...] = ()
    #: Scopes beneath which every rule in force defers, each with the rules it does not reach. It is
    #: one declaration, where an edit per rule would be forgotten by the next.
    deferrals: tuple[Deferral, ...] = ()


@dataclass(frozen=True, slots=True)
class Deferral:
    """Every rule in force defers beneath what one folder rule raises, except those named.

    A copy of an installed program, such as a Fiji build, holds files that the rules would otherwise
    read as a laboratory's data. A rule that a deferral reaches names ``scope`` as a defeater. So
    what the rule concludes beneath a folder that the scope raised is published as conflicting. A
    class there groups nothing firm, a pair there is blocked, and a finding there is raised and
    marked. The result is shown and never excluded. The rules in ``exempt`` state a physical fact
    that is as true inside a program as outside it, and each carries its reason in the revision.
    """

    scope: str
    exempt: tuple[str, ...] = ()
    rationale: str = ""


def _deferrals_from(declared: object, *, where: str) -> tuple[Deferral, ...]:
    """Read a revision's ``defer_beneath`` list, refusing anything that is not one."""
    if declared is None:
        return ()
    if not isinstance(declared, list):
        message = f"{where}: 'defer_beneath' must be a list"
        raise RuleDeclarationError(message)
    found: list[Deferral] = []
    for item in declared:
        if not isinstance(item, dict):
            message = f"{where}: each deferral must be a mapping"
            raise RuleDeclarationError(message)
        unexpected = sorted(set(item) - {"scope", "except", "rationale"})
        if unexpected:
            message = f"{where}: a deferral carries {', '.join(unexpected)}, which nothing reads"
            raise RuleDeclarationError(message)
        scope = str(item.get("scope") or "").strip()
        if not scope:
            message = f"{where}: a deferral names the scope rule every other rule defers beneath"
            raise RuleDeclarationError(message)
        exempt = item.get("except") or []
        if not isinstance(exempt, list) or not all(isinstance(name, str) for name in exempt):
            message = f"{where}: a deferral's 'except' is a list of rule identifiers"
            raise RuleDeclarationError(message)
        rationale = str(item.get("rationale") or "").strip()
        if not rationale:
            message = (
                f"{where}: the deferral beneath {scope!r} says why; a change reaching every rule "
                "in force is one a person must be able to read the reason for"
            )
            raise RuleDeclarationError(message)
        found.append(Deferral(scope=scope, exempt=tuple(sorted(set(exempt))), rationale=rationale))
    return tuple(found)


def load_project_revision(path: Path, *, scope: Scope = PROJECT_SCOPE) -> ProjectRevision:
    """Read and validate one project revision file."""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return project_revision_from(document, name=path.name, scope=scope)


def project_revision_from(
    document: object, *, name: str, scope: Scope = PROJECT_SCOPE
) -> ProjectRevision:
    """Validate one project revision, from a file or from what the retired file kept."""
    path = Path(name)
    if not isinstance(document, dict):
        message = f"{path.name}: a project revision file must be a mapping"
        raise RuleDeclarationError(message)

    number = document.get(PROJECT_REVISION_KEY)
    if (
        "revision" in document
        or isinstance(number, bool)
        or not isinstance(number, int)
        or number < 1
    ):
        message = (
            f"{path.name}: a project revision carries a positive integer "
            f"{PROJECT_REVISION_KEY!r} and numbers itself in a sequence of its own; "
            f"'revision' numbers the shipped Rulebook"
        )
        raise RuleDeclarationError(message)

    where = f"{path.name}: project revision {number}"
    declared_changes = document.get("changes") or []
    declarations = document.get("rules") or []
    if not isinstance(declared_changes, list) or not isinstance(declarations, list):
        message = f"{where}: 'changes' and 'rules' must each be a list"
        raise RuleDeclarationError(message)

    changes = tuple(_change_from(item, where=where) for item in declared_changes)
    rules = tuple(
        _rule_from(
            {**declaration, "origin": "project"} if isinstance(declaration, dict) else declaration,
            path=path,
            revision=number,
            scope=scope,
        )
        for declaration in declarations
    )
    _refuse_duplicate_identifiers(rules, path=path)
    _refuse_what_only_a_shipped_rule_may_be(rules, where=where)
    named = [change.rule_id for change in changes] + [rule.rule_id for rule in rules]
    repeated = sorted({name for name in named if named.count(name) > 1})
    if repeated:
        message = (
            f"{where}: names {', '.join(repeated)} more than once; one revision says one "
            f"thing about a rule, and two would compose in whatever order they were listed"
        )
        raise RuleDeclarationError(message)
    return ProjectRevision(
        number=number,
        summary=str(document.get("summary", "")),
        changes=changes,
        rules=rules,
        deferrals=_deferrals_from(document.get("defer_beneath"), where=where),
    )


def load_project_revisions(
    directory: Path, *, scope: Scope = PROJECT_SCOPE
) -> tuple[ProjectRevision, ...]:
    """Load a project's own revisions, in the order their numbers give them.

    A project with no revisions directory, or an empty one, has none, which is how
    every project starts. Two files sharing a number are refused for the reason two
    shipped revisions are: composition order would depend on a directory listing.
    The retired rules file (:mod:`ingence.rulebook.retired`) is read as the choices
    a person made in it, and nothing else.
    """
    if not directory.is_dir():
        return ()
    from ingence.rulebook import retired

    revisions: list[ProjectRevision] = []
    for path in sorted(directory.glob("*.yaml")):
        # The retired rules file is skipped whole. Its rules are the engine's own now, and what a
        # person chose in it composes at its number instead.
        if path.name == retired.FILE_NAME and (found := retired.read_retired(directory)):
            if found.holds_choices:
                revisions.append(retired.kept_revision(found, scope=scope))
            continue
        revisions.append(load_project_revision(path, scope=scope))
    seen: set[int] = set()
    for revision in revisions:
        if revision.number in seen:
            message = (
                f"project revision {revision.number} is declared twice; composition order "
                f"would then depend on the order the filesystem returned names"
            )
            raise RuleDeclarationError(message)
        seen.add(revision.number)
    return tuple(sorted(revisions, key=lambda revision: revision.number))


def _refuse_what_only_a_shipped_rule_may_be(rules: Sequence[Rule], *, where: str) -> None:
    """Refuse a project's own rule that enumerates, or that declares itself provisional.

    The scan layer runs the rules it was built with, so a project rule that enumerates is a
    declaration that nothing would ever run. Provisional marks a rule that a person has yet to
    confirm. A project's own rule is in force as its author wrote it, and a rule declared
    provisional could never be switched off by a later project revision.
    """
    for rule in rules:
        if rule.effect is RuleEffect.ENUMERATES:
            message = (
                f"{where}: {rule.rule_id!r} enumerates, and the scan layer runs only the rules "
                f"it was built with, so nothing would ever run it"
            )
            raise RuleDeclarationError(message)
        if rule.status is RuleStatus.PROVISIONAL:
            message = (
                f"{where}: declares {rule.rule_id!r} provisional; a project's own rule is in "
                f"force as its author wrote it, and one marked provisional could never be "
                f"switched off by a later revision"
            )
            raise RuleDeclarationError(message)


def _change_from(item: object, *, where: str) -> ProjectChange:
    """Read one change, refusing one that does not say exactly one thing about one rule."""
    if not isinstance(item, dict):
        message = f"{where}: each change must be a mapping"
        raise RuleDeclarationError(message)
    verbs = [verb for verb in ChangeVerb if verb.value in item]
    if len(verbs) != 1:
        permitted = ", ".join(verb.value for verb in ChangeVerb)
        message = f"{where}: each change names exactly one of {permitted}"
        raise RuleDeclarationError(message)
    verb = verbs[0]
    rule_id = str(item[verb.value] or "").strip()
    if not rule_id:
        message = f"{where}: {verb.value} must name a rule"
        raise RuleDeclarationError(message)
    unexpected = sorted(set(item) - {verb.value, "parameters"})
    if unexpected:
        message = (
            f"{where}: the change to {rule_id!r} carries {', '.join(unexpected)}, "
            f"which no change reads"
        )
        raise RuleDeclarationError(message)
    parameters = item.get("parameters")
    if verb is not ChangeVerb.OVERRIDE:
        if parameters is not None:
            message = (
                f"{where}: {verb.value} {rule_id!r} carries parameters, and only an "
                f"override sets parameters"
            )
            raise RuleDeclarationError(message)
        return ProjectChange(verb=verb, rule_id=rule_id)
    if not isinstance(parameters, dict) or not parameters:
        message = f"{where}: the override of {rule_id!r} must set at least one parameter"
        raise RuleDeclarationError(message)
    return ProjectChange(
        verb=verb,
        rule_id=rule_id,
        parameters={str(name): value for name, value in parameters.items()},
    )


# ---------------------------------------------------------------------------
# Building and refusing one rule.
# ---------------------------------------------------------------------------


def _rule_from(declaration: object, *, path: Path, revision: int, scope: Scope) -> Rule:
    """Build one rule from its declaration, refusing anything that is not one."""
    if not isinstance(declaration, dict):
        message = f"{path.name}: each rule must be a mapping"
        raise RuleDeclarationError(message)

    rule_id = str(declaration.get("id", "")).strip()
    if not rule_id:
        message = f"{path.name}: a rule must carry an 'id'"
        raise RuleDeclarationError(message)

    where = f"{path.name}: rule {rule_id!r}"
    predicate = str(declaration.get("predicate", "")).strip()
    effect = _effect_from(declaration, where=where)
    parameters = _parameters_from(declaration.get("parameters"), where=where)
    statement = str(declaration.get("statement", "")).strip()

    _refuse_unregistered_predicate(predicate, where=where)
    _refuse_mismatched_effect(predicate, effect, where=where)
    _refuse_temporal_support_on_a_proposing_rule(predicate, effect, where=where)
    _refuse_unfilled_or_unused_slots(statement, parameters, where=where)
    _refuse_parameters_its_predicate_does_not_read(predicate, parameters, where=where)
    composition = _composition_from(declaration.get("composition"), where=where)
    primitive, specialization, respect = _relation_from(declaration, effect, where=where)
    defeated_by = _defeaters_from(declaration.get("defeated_by"), where=where)
    candidates_within = _index_from(declaration.get("candidates_within"), effect, where=where)
    rationale = str(declaration.get("rationale", "")).strip()
    if not rationale:
        message = f"{where}: a rule must carry a 'rationale', the reason it exists"
        raise RuleDeclarationError(message)

    tier = _tier_from(declaration, where=where)
    status = _status_from(declaration, where=where)
    try:
        return Rule(
            rule_id=rule_id,
            version=str(declaration.get("version", "1")),
            primitive=primitive,
            specialization=specialization,
            respect=respect,
            statement=statement,
            parameters=parameters,
            scope=scope,
            level=str(declaration.get("level", "file")),
            predicate=predicate,
            predicate_version=str(PREDICATES.declaration(predicate).version),
            effect=effect,
            tier=tier,
            defeated_by=defeated_by,
            candidates_within=candidates_within,
            enabled=bool(declaration.get("enabled", True)),
            status=status,
            rationale=rationale,
            composition=composition,
            origin=str(declaration.get("origin", "builtin")),
            added_revision=int(declaration.get("added_revision", revision)),
            superseded_revision=declaration.get("superseded_revision"),
        )
    except ValidationError as error:
        # Whatever else the Rule model refuses is said in words, at the rule. A person who writes a
        # rule by hand reads a sentence, not a traceback.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in found['loc']) or 'the rule'}: {found['msg']}"
            for found in error.errors()
        )
        message = f"{where}: the rule is not valid ({problems})"
        raise RuleDeclarationError(message) from None


def _tier_from(declaration: Mapping[str, Any], *, where: str) -> InferenceTier:
    """Return the declared tier, defeasible where none is declared, refusing an unknown one."""
    raw = str(declaration.get("tier", InferenceTier.DEFEASIBLE.value)).strip()
    try:
        return InferenceTier(raw)
    except ValueError:
        permitted = ", ".join(sorted(member.value for member in InferenceTier))
        message = f"{where}: tier {raw!r} is not one of {permitted}"
        raise RuleDeclarationError(message) from None


def _defeaters_from(declared: object, *, where: str) -> tuple[str, ...]:
    """Read the rules that a rule names as what would defeat its claims.

    A defeat condition is named by identifier and not by sentence. It is a rule with a sentence of
    its own, so a rule file that names one points at something a person can open. Whether each
    identifier names a rule that exists is checked when the Rulebook is composed, which is the first
    moment every revision is in view.
    """
    if declared is None:
        return ()
    if not isinstance(declared, list) or not all(
        isinstance(name, str) and name.strip() for name in declared
    ):
        message = f"{where}: 'defeated_by' must be a list of rule identifiers"
        raise RuleDeclarationError(message)
    return tuple(name.strip() for name in declared)


def _index_from(declared: object, effect: RuleEffect, *, where: str) -> str | None:
    """Read the index a proposing rule draws its pairs within, refusing it on any other rule.

    Only a proposing rule draws pairs, so an index on a rule of any other effect is a declaration
    that nothing could ever read, and the person who wrote it expected something to happen. Whether
    it names a grouping rule that exists is checked when the Rulebook is composed, which is the
    first moment every revision is in view.
    """
    from ingence.rulebook.registry import CO_MENTION_INDEX, MENTION_INDEX, PARENT_INDEX

    if declared is None:
        return None
    if not isinstance(declared, str) or not declared.strip():
        message = (
            f"{where}: 'candidates_within' must name a grouping rule, {PARENT_INDEX!r} "
            f"for the files of one folder, {MENTION_INDEX!r} for a file and what its mentions "
            f"name, or {CO_MENTION_INDEX!r} for what one file's mentions name together"
        )
        raise RuleDeclarationError(message)
    if effect is not RuleEffect.PROPOSES:
        message = (
            f"{where}: names candidates_within and {effect.value}; only a proposing rule "
            f"draws candidate pairs within an index"
        )
        raise RuleDeclarationError(message)
    return declared.strip()


def _relation_from(
    declaration: Mapping[str, Any], effect: RuleEffect, *, where: str
) -> tuple[Primitive | None, str | None, str | None]:
    """Resolve what relation a rule asserts, refusing a declaration that cannot.

    A rule that proposes an edge or defines a grouping must say which of the eight primitives it
    means. Without it the rule is a sentence about a relationship the engine has no name for, and
    there is no generic ``related_to`` to fall back on: where the engine cannot say how two things
    relate it abstains, and a rule is the wrong place for that abstention because the person who
    wrote it did know.

    The respect is derived from the specialization where one is named, and stated directly where
    none is. ``replicate_of`` fixes the respect ``experimental_condition`` in the catalog, so
    writing it again in the rule file would be a second place for it to be wrong, and the one
    nothing reads would be the one that drifted. The same reasoning that derives a clock's tier from
    its source rather than storing both.

    A classifying, flagging or constraining rule may name a primitive and mostly will not.
    ``derivation_is_acyclic`` concerns ``derives`` and says so; a rule raising a folder for
    attention concerns no relation at all.
    """
    primitive = _primitive_from(declaration, where=where)
    specialization = _specialization_from(declaration, primitive, where=where)
    declared_respect = declaration.get("respect")
    declared_respect = None if declared_respect is None else str(declared_respect).strip()

    if effect in {RuleEffect.PROPOSES, RuleEffect.GROUPS} and primitive is None:
        message = (
            f"{where}: {effect.value} and names no primitive; a rule that asserts a "
            f"relationship must say which of the eight it means, because there is no "
            f"generic related_to to fall back on"
        )
        raise RuleDeclarationError(message)

    if effect is RuleEffect.GROUPS and primitive is not None:
        _refuse_a_grouping_on_a_primitive_that_is_not_an_equivalence(primitive, where=where)

    if specialization is not None:
        fixed = _SPECIALIZATIONS.get(specialization).respect
        if declared_respect is not None and declared_respect != fixed:
            message = (
                f"{where}: states respect {declared_respect!r} and specialization "
                f"{specialization!r} fixes {fixed!r}; the respect comes from the "
                f"specialization so that the two can never disagree"
            )
            raise RuleDeclarationError(message)
        return primitive, specialization, fixed

    return primitive, specialization, declared_respect


def _primitive_from(declaration: Mapping[str, Any], *, where: str) -> Primitive | None:
    """Return the declared primitive, refusing an unknown one by name."""
    raw = declaration.get("primitive")
    if raw is None:
        return None
    try:
        return Primitive(str(raw).strip())
    except ValueError:
        permitted = ", ".join(member.value for member in Primitive)
        message = (
            f"{where}: primitive {raw!r} is not one of the eight: {permitted}. "
            f"The set is closed; adding a ninth is a formal-property argument, not a rule"
        )
        raise RuleDeclarationError(message) from None


def _specialization_from(
    declaration: Mapping[str, Any], primitive: Primitive | None, *, where: str
) -> str | None:
    """Return the declared specialization, refusing one the catalog does not hold.

    Refused rather than registered on the fly, because two specializations under one name is the
    vocabulary fragmentation the closed primitive set exists to prevent, and a rule file is exactly
    where an ad-hoc name would be introduced. A project that genuinely needs one adds it to its own
    registry, which is a decision somebody takes rather than a typo that succeeds.
    """
    raw = declaration.get("specialization")
    if raw is None:
        return None
    name = str(raw).strip()
    if name not in _SPECIALIZATIONS:
        message = (
            f"{where}: specialization {name!r} is not in the catalog; an ad-hoc "
            f"relation name is the fragmentation the closed primitive set prevents, "
            f"so add it to the catalog or state a primitive and a respect directly"
        )
        raise RuleDeclarationError(message)
    entry: Specialization = _SPECIALIZATIONS.get(name)
    if primitive is not None and entry.parent is not primitive:
        message = (
            f"{where}: specialization {name!r} has parent {entry.parent.value!r} and the "
            f"rule declares {primitive.value!r}; a specialization inherits every formal "
            f"property of its parent and may not contradict it"
        )
        raise RuleDeclarationError(message)
    return name


def _refuse_a_grouping_on_a_primitive_that_is_not_an_equivalence(
    primitive: Primitive, *, where: str
) -> None:
    """A grouping rule needs a relation that actually partitions something.

    Read off the declared formal properties rather than from a list of the two primitives that
    qualify. A grouping is a quotient, a quotient needs an equivalence, and an equivalence is
    symmetric and transitive. Naming ``equivalent`` and ``identical`` directly would be a third
    place the signatures are recorded, and it would silently permit ``concerns``, which is symmetric
    and never transitive, on exactly the day somebody wrote a rule grouping by shared subject
    matter. That grouping would be wrong in a way no output could reveal: A concerns B and B
    concerns C puts A and C in one group with nothing asserting they are related.
    """
    properties = PRIMITIVE_PROPERTIES[primitive]
    if properties.symmetric and properties.transitive:
        return
    held = (("symmetric", properties.symmetric), ("transitive", properties.transitive))
    missing = [name for name, holds in held if not holds]
    message = (
        f"{where}: groups on {primitive.value!r}, which is not {' or '.join(missing)}; "
        f"a grouping is a quotient and a quotient needs an equivalence relation"
    )
    raise RuleDeclarationError(message)


def _effect_from(declaration: Mapping[str, Any], *, where: str) -> RuleEffect:
    """Return the declared effect, refusing an unknown one by name."""
    raw = str(declaration.get("effect", "")).strip()
    try:
        return RuleEffect(raw)
    except ValueError:
        permitted = ", ".join(sorted(member.value for member in RuleEffect))
        message = f"{where}: effect {raw!r} is not one of {permitted}"
        raise RuleDeclarationError(message) from None


def _status_from(declaration: Mapping[str, Any], *, where: str) -> RuleStatus:
    """Return the declared status, active where none is declared, refusing an unknown one."""
    raw = str(declaration.get("status", RuleStatus.ACTIVE.value)).strip()
    try:
        return RuleStatus(raw)
    except ValueError:
        permitted = ", ".join(sorted(member.value for member in RuleStatus))
        message = f"{where}: status {raw!r} is not one of {permitted}"
        raise RuleDeclarationError(message) from None


def _parameters_from(declared: object, *, where: str) -> tuple[ParameterSpec, ...]:
    """Build the parameter specifications, refusing a default its own bounds reject."""
    if declared is None:
        return ()
    if not isinstance(declared, list):
        message = f"{where}: 'parameters' must be a list"
        raise RuleDeclarationError(message)

    specifications: list[ParameterSpec] = []
    for item in declared:
        if not isinstance(item, dict):
            message = f"{where}: each parameter must be a mapping"
            raise RuleDeclarationError(message)
        name = str(item.get("name", "")).strip()
        if not name:
            message = f"{where}: a parameter must carry a 'name'"
            raise RuleDeclarationError(message)
        try:
            kind = ParameterType(str(item.get("type", "")).strip())
        except ValueError:
            permitted = ", ".join(sorted(member.value for member in ParameterType))
            declared_type = item.get("type")
            message = (
                f"{where}: parameter {name!r} has type {declared_type!r}, not one of {permitted}"
            )
            raise RuleDeclarationError(message) from None

        choices = tuple(str(choice) for choice in item.get("choices", ()) or ())
        minimum = item.get("minimum")
        maximum = item.get("maximum")
        # Canonically serialized, because a default can be a list or a mapping and
        # the record stores one string. Serialising here rather than asking the
        # file to quote its own JSON keeps the file readable.
        default = canonical_json(item.get("default"))
        specification = ParameterSpec(
            name=name,
            type=kind,
            default=default,
            choices=choices,
            minimum=None if minimum is None else float(minimum),
            maximum=None if maximum is None else float(maximum),
            description=str(item.get("description", "")).strip(),
        )
        _refuse_default_outside_its_own_bounds(specification, item.get("default"), where=where)
        specifications.append(specification)
    return tuple(specifications)


def _refuse_default_outside_its_own_bounds(
    specification: ParameterSpec, default: object, *, where: str
) -> None:
    """Refuse a parameter whose own default its own declaration rejects.

    The check matters because the failure is silent in the other direction. A rule whose default is
    out of range produces a conclusion at a setting that the interface will not let anybody
    reproduce. A parameter sweep would then start from a point outside its own axis.
    """
    name = specification.name
    if specification.type is ParameterType.ENUM:
        if not specification.choices:
            message = f"{where}: enum parameter {name!r} declares no choices"
            raise RuleDeclarationError(message)
        if str(default) not in specification.choices:
            permitted = ", ".join(specification.choices)
            message = f"{where}: default {default!r} for {name!r} is not one of {permitted}"
            raise RuleDeclarationError(message)
        return

    if specification.type not in {
        ParameterType.INTEGER,
        ParameterType.REAL,
        ParameterType.DURATION,
    }:
        return

    if not isinstance(default, (int, float)) or isinstance(default, bool):
        message = f"{where}: parameter {name!r} is numeric and its default {default!r} is not"
        raise RuleDeclarationError(message)
    if specification.minimum is not None and default < specification.minimum:
        message = (
            f"{where}: default {default} for {name!r} is below its minimum {specification.minimum}"
        )
        raise RuleDeclarationError(message)
    if specification.maximum is not None and default > specification.maximum:
        message = (
            f"{where}: default {default} for {name!r} is above its maximum {specification.maximum}"
        )
        raise RuleDeclarationError(message)


def refuse_a_value_its_parameter_refuses(
    specification: ParameterSpec, value: object, *, where: str
) -> None:
    """Refuse a value that a parameter's own declaration rejects.

    The value can be the wrong type, a choice that the parameter does not offer, or a number outside
    its bounds. It is held to the bounds that a declared default is held to, because a project that
    overrides a parameter outside them would produce conclusions at a setting that the interface
    will not let anybody reproduce.
    """
    name = specification.name
    kind = specification.type
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    accepted = {
        ParameterType.BOOLEAN: isinstance(value, bool),
        ParameterType.INTEGER: numeric and isinstance(value, int),
        ParameterType.REAL: numeric,
        ParameterType.DURATION: numeric,
        ParameterType.STRING: isinstance(value, str),
        ParameterType.ENUM: isinstance(value, str),
        ParameterType.STRING_LIST: isinstance(value, list)
        and all(isinstance(item, str) for item in value),
        ParameterType.MAPPING: isinstance(value, dict)
        and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()),
    }[kind]
    if not accepted:
        message = f"{where}: parameter {name!r} is {kind.value} and {value!r} is not"
        raise RuleDeclarationError(message)
    if kind is ParameterType.ENUM and value not in specification.choices:
        permitted = ", ".join(specification.choices)
        message = f"{where}: {value!r} for {name!r} is not one of {permitted}"
        raise RuleDeclarationError(message)
    bounded = {ParameterType.INTEGER, ParameterType.REAL, ParameterType.DURATION}
    if kind not in bounded or isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    if specification.minimum is not None and value < specification.minimum:
        message = f"{where}: {value} for {name!r} is below its minimum {specification.minimum:g}"
        raise RuleDeclarationError(message)
    if specification.maximum is not None and value > specification.maximum:
        message = f"{where}: {value} for {name!r} is above its maximum {specification.maximum:g}"
        raise RuleDeclarationError(message)


def _composition_from(declared: object, *, where: str) -> RuleComposition | None:
    """Build the boolean composition, refusing a predicate nobody registered."""
    if declared is None:
        return None
    if not isinstance(declared, dict):
        message = f"{where}: 'composition' must be a mapping"
        raise RuleDeclarationError(message)

    parts = {
        key: tuple(str(item) for item in declared.get(key, ()) or ())
        for key in ("all_of", "any_of", "none_of")
    }
    for key, names in parts.items():
        for name in names:
            _refuse_unregistered_predicate(name, where=f"{where}: composition {key}")
    return RuleComposition(**parts)


def _refuse_unregistered_predicate(predicate: str, *, where: str) -> None:
    """Refuse a rule naming a predicate nothing registered.

    Loudly, rather than by disabling the rule. A Rulebook that silently drops a
    rule produces different answers than the file says it will, and the person
    reading the file has no way to tell.
    """
    if not predicate:
        message = f"{where}: a rule must name a 'predicate'"
        raise RuleDeclarationError(message)
    if predicate not in PREDICATES.names():
        known = ", ".join(PREDICATES.names()) or "nothing"
        message = f"{where}: predicate {predicate!r} is not registered; registered: {known}"
        raise RuleDeclarationError(message)


def _refuse_mismatched_effect(predicate: str, effect: RuleEffect, *, where: str) -> None:
    """Refuse a rule whose declared effect its predicate does not serve, or cannot feed.

    A rule's effect decides where in the pipeline it runs, so ordering is derived and not
    configured. A rule that declares one effect and calls a predicate that serves another would run
    in the wrong phase, and the symptom would be an absent conclusion and not an error.

    The effect also decides what the predicate is handed. A predicate that reads a shape its rule's
    effect never hands over could only ever abstain. That abstention would look exactly like a
    predicate that found nothing, so it is refused here, where the person who can fix it is reading
    the file.
    """
    declaration = PREDICATES.declaration(predicate)
    served = effect_of(declaration)
    if served is not effect:
        message = (
            f"{where}: declares effect {effect.value!r} and its predicate serves "
            f"{served.value!r}; effect decides which phase a rule runs in"
        )
        raise RuleDeclarationError(message)
    try:
        reads = reads_of(declaration)
    except ValueError as exc:
        message = f"{where}: {exc}"
        raise RuleDeclarationError(message) from None
    handed = READS_BY_EFFECT[effect]
    if reads not in handed:
        shapes = ", ".join(sorted(shape.value for shape in handed))
        message = (
            f"{where}: its predicate reads {reads.value!r} and a {effect.value} rule is "
            f"handed one of {shapes}; it could only ever abstain on what it was given"
        )
        raise RuleDeclarationError(message)


def _refuse_temporal_support_on_a_proposing_rule(
    predicate: str, effect: RuleEffect, *, where: str
) -> None:
    """Enforce the constraint that burst or window identifiers never score a proposing rule.

    Temporal overlap is not evidence of relatedness. Two unrelated projects running in parallel
    overlap perfectly, and every laboratory has months where three unconnected efforts were active.
    A burst may bound candidate generation, which narrows what is considered, and may be displayed.
    It may never make a proposed edge more credible.

    The check is made against the predicate's declared families and not against its implementation.
    The declaration is available before the predicate is evaluable, and a rule must be refusable
    before anything can run it.
    """
    if effect is not RuleEffect.PROPOSES:
        return
    families = frozenset(families_of(PREDICATES.declaration(predicate)))
    offending = sorted(families & TEMPORAL_FAMILIES)
    if offending:
        message = (
            f"{where}: proposes an edge and scores on {', '.join(offending)}; "
            f"temporal overlap is not evidence of relatedness, so a burst or a window "
            f"may bound candidates and may be displayed, and may never contribute support"
        )
        raise RuleDeclarationError(message)


def _refuse_unfilled_or_unused_slots(
    statement: str, parameters: Sequence[ParameterSpec], *, where: str
) -> None:
    """Refuse a sentence and a parameter list that do not describe each other.

    Both directions, and each catches a different lie. A slot with no parameter renders as a literal
    brace and the sentence stops being English. A parameter with no slot is a knob whose setting the
    sentence never mentions, so a person reading the rendered Rulebook cannot see what the engine is
    actually doing, which is the attributability claim failing quietly.
    """
    if not statement:
        message = f"{where}: a rule must carry a 'statement'"
        raise RuleDeclarationError(message)
    slots = {match.group(1) for match in _SLOT.finditer(statement)}
    declared = {specification.name for specification in parameters}
    unfilled = sorted(slots - declared)
    if unfilled:
        message = f"{where}: statement names {', '.join(unfilled)}, which no parameter declares"
        raise RuleDeclarationError(message)
    unmentioned = sorted(declared - slots)
    if unmentioned:
        message = (
            f"{where}: declares {', '.join(unmentioned)}, which the statement never mentions; "
            f"a parameter the sentence hides is a setting nobody reading the rule can see"
        )
        raise RuleDeclarationError(message)


def _refuse_parameters_its_predicate_does_not_read(
    predicate: str, parameters: Sequence[ParameterSpec], *, where: str
) -> None:
    """Refuse a parameter that the rule's predicate does not declare it reads.

    A parameter that its predicate ignores is a setting that changes nothing while the sentence says
    it does. The statement and the parameters are held to each other. Without this check, nothing
    would hold the parameters to the predicate, and a rule that set a parameter its predicate never
    read would load, run as though the setting were absent, and render as though it were in force.
    """
    reads = parameters_of(PREDICATES.declaration(predicate))
    unread = sorted({specification.name for specification in parameters} - reads)
    if unread:
        listed = ", ".join(sorted(reads)) or "no parameter"
        message = (
            f"{where}: declares {', '.join(unread)}, which predicate {predicate!r} does not "
            f"read; it reads {listed}"
        )
        raise RuleDeclarationError(message)


def _refuse_duplicate_identifiers(rules: Sequence[Rule], *, path: Path) -> None:
    """Refuse one revision declaring a rule identifier twice.

    Within a revision, not across them: a later revision naming an earlier rule is
    how superseding works and is the point of the design.
    """
    seen: set[tuple[str, str]] = set()
    for rule in rules:
        key = (rule.rule_id, rule.version)
        if key in seen:
            message = f"{path.name}: rule {rule.rule_id!r} version {rule.version!r} declared twice"
            raise RuleDeclarationError(message)
        seen.add(key)
