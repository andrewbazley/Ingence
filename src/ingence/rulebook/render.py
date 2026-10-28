"""Rendering a rule as the sentence a person reads.

The English sentence and the executable form are two renderings of one object, not two objects kept
in sync. That is the whole design, and the reason it matters is the project's central claim: every
conclusion traces to a named, versioned rule that a user can read, tune, or switch off. A rule whose
documentation is written separately from its declaration drifts, and a drifted rule is one a user
approved on the strength of a sentence that no longer describes it.

So the statement is a template with typed slots, the slots are the rule's own parameters, and the
loader already refuses a statement and a parameter list that do not describe each other in both
directions. Rendering is then filling in, and it cannot disagree with what runs because there is
nothing to disagree with.

A parameter is shown at the value in force, not at its default. The point of rendering is to say
what the engine is doing, and a rule whose threshold a user raised should read at the raised value.
Where nothing has overridden it the default is the value in force, which is the common case and not
a special one.

Lists and mappings render as English, not as JSON. A parameter holding three extensions reads as
"``.tif``, ``.tiff`` or ``.png``" rather than as a bracketed array, because the sentence is for a
person and a person reading a bracketed array has been handed the implementation.
"""

from __future__ import annotations

import json
import re
from string import Formatter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.core.schema import ParameterSpec, Rule
    from ingence.rulebook.resolve import EffectiveRulebook

__all__ = ["plain_reason", "render_rule", "render_rulebook", "value_in_force"]


def render_rule(rule: Rule, *, overrides: Mapping[str, object] | None = None) -> str:
    """Return one rule's sentence with its parameters filled in.

    ``overrides`` carries the values a project set, where it set any. Absent, the
    rule renders at its declared defaults, which is what it will do.
    """
    specifications = {specification.name: specification for specification in rule.parameters}
    pieces: list[str] = []
    for literal, slot, _, _ in Formatter().parse(rule.statement):
        pieces.append(literal)
        if slot is not None:
            value = value_in_force(specifications[slot], overrides)
            pieces.append(_english(value, conjunction=_conjunction_before("".join(pieces))))
    return "".join(pieces)


def render_rulebook(
    rulebook: EffectiveRulebook,
    *,
    overrides: Mapping[str, Mapping[str, object]] | None = None,
    revision_asked: bool = False,
) -> str:
    """Return the whole effective Rulebook as a document a person can read.

    Grouped by effect, because effect decides which phase a rule runs in, so
    grouping by it is the order the engine actually applies them. A document
    ordered alphabetically would read as a glossary; ordered by phase it reads as
    what happens to a file, which is the question a user has.

    Rules that exist and are not in force are listed with why, because "your rule
    was superseded at revision 5" and "there is no such rule" are answers to
    different questions.
    """
    from ingence.core.schema import RuleStatus

    per_rule = overrides or {}
    # In words, and not as bookkeeping: the book's revision numbers once named a separate set of
    # rules, and a person's own changes are "your project's rules".
    heading = "Rules in force" + (f" at revision {rulebook.revision}" if revision_asked else "")
    if rulebook.project_revision:
        heading = f"{heading}, with your project's rules"
    lines: list[str] = [heading, f"digest {rulebook.digest}", ""]
    deferring = {rule_id: scope for rule_id, scope, _ in rulebook.shipped_deferred}
    deferring.update({rule_id: scope for rule_id, scope, _ in rulebook.deferred})
    by_effect: dict[str, list[Rule]] = {}
    for rule in rulebook.rules:
        by_effect.setdefault(rule.effect.value, []).append(rule)

    from ingence.rulebook.availability import NOT_AVAILABLE, unavailable_rules

    unavailable = unavailable_rules(rulebook.rules)
    for effect in sorted(by_effect):
        lines.append(f"{effect}:")
        for rule in by_effect[effect]:
            sentence = render_rule(rule, overrides=per_rule.get(rule.rule_id))
            # Provisional is said where it holds and nowhere else, so every confirmed
            # rule reads exactly as it did before a rule could be anything else.
            marks = [rule.tier.value]
            if rule.status is RuleStatus.PROVISIONAL:
                marks.append(rule.status.value)
            lines.append(f"  {rule.rule_id} (v{rule.version}, {', '.join(marks)}) {sentence}")
            if rule.rule_id in unavailable:
                lines.append(f"    {NOT_AVAILABLE}")
            if rule.rule_id in deferring:
                lines.append(
                    f"    and defers beneath every folder {deferring[rule.rule_id]} raises"
                )
            lines.append(f"    because {_after_because(rule.rationale)}")
        lines.append("")

    if rulebook.inactive:
        lines.append("not in force:")
        lines.extend(
            f"  {rule_id}: {plain_reason(reason)}" for rule_id, reason in rulebook.inactive
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


#: Capitalised words that stay capitalised in the middle of a sentence: the engine's own terms and
#: the formats and programs its rules name.
_KEEP_CAPITAL = frozenset(
    {"Dictionary", "Rulebook", "Index", "Record", "Dashboard", "Graph", "Fiji", "Python", "Wilson"}
)


def _after_because(rationale: str) -> str:
    """A rationale read after "because": its first word lowercase unless it is a name or a code."""
    first = rationale.split(" ", 1)[0]
    plain = first.isalpha() and first not in _KEEP_CAPITAL
    if plain and (first == "A" or (first[:1].isupper() and first[1:].islower())):
        return rationale[:1].lower() + rationale[1:]
    return rationale


def value_in_force(
    specification: ParameterSpec, overrides: Mapping[str, object] | None = None
) -> object:
    """Return the value a parameter actually has, decoded from its stored form.

    A default is stored canonically serialized, because it can be a list or a
    mapping and the record holds one string. Decoding here rather than at every
    reader means a consumer sees the value, and only this module has to know that
    the storage form is JSON.
    """
    if overrides is not None and specification.name in overrides:
        return overrides[specification.name]
    try:
        return json.loads(specification.default)
    except json.JSONDecodeError:
        # A default written as a bare word rather than as JSON. Returned as
        # written rather than refused, because refusing at render time would make
        # a display concern break a run, and the loader is where a malformed
        # declaration is caught.
        return specification.default


#: The words that, written right before a list's slot, make the list a conjunction. "all of"
#: is the one a shipped sentence uses; a list after any other words is a disjunction.
_CONJUNCTIVE: tuple[str, ...] = ("all of", "each of", "both")


def _conjunction_before(written: str) -> str:
    """Return the word a list joins with, read from the sentence written before its slot.

    A list after "all of" joins with "and", and every other list with "or". The
    template already says which the rule means, so the word is read from it rather than
    declared beside the parameter, where it would be a second copy of the sentence and
    could come to disagree with it.
    """
    words = " ".join(written.split()).casefold()
    conjunctive = any(words == phrase or words.endswith(f" {phrase}") for phrase in _CONJUNCTIVE)
    return "and" if conjunctive else "or"


def _english(value: object, *, conjunction: str = "or") -> str:
    """Render one parameter value as a person would write it.

    Lists join with "or" rather than with commas alone, because a rule saying "a
    file whose extension is .tif, .tiff" reads as a conjunction and means a
    disjunction; a list the sentence asks all of joins with "and", which
    :func:`_conjunction_before` decides. Mappings render as arrows, which is how an
    extension-to-role table is written on a whiteboard.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ", ".join(f"{key} to {inner}" for key, inner in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        items = [_english(item) for item in value]
        if not items:
            return "nothing"
        if len(items) == 1:
            return items[0]
        return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"
    return _number(value)


def _number(value: Any) -> str:
    """Render a number without a trailing zero nobody wrote.

    ``3.0`` came from a real parameter and reads as ``3``. A rule sentence saying
    "at least 3.0 files" states a precision the rule does not have.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


#: Why a rule is out of force, in words a person reads. The Rulebook's own reasons name its
#: bookkeeping ("superseded at revision 4", "disabled at project revision 2"). The Rules page and
#: ``rule show`` say what happened instead.
_PLAIN_REASONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^superseded at project revision \d+$"), "replaced in your project's rules"),
    (re.compile(r"^superseded at revision \d+$"), "replaced by a newer rule"),
    (re.compile(r"^disabled at project revision \d+$"), "switched off in your project's rules"),
    (re.compile(r"^disabled( at revision \d+)?$"), "switched off"),
)


def plain_reason(reason: str) -> str:
    """The Rulebook's reason a rule is out of force, as the Rules page says it."""
    for pattern, words in _PLAIN_REASONS:
        if pattern.match(reason):
            return words
    return reason
