"""``/api/v1/rules/copy`` and ``/api/v1/dictionary/copy``: what the Copy buttons copy.

The Dashboard's rule card says "Copy as a revision" and a word's card "Copy as YAML". What
they copy is the text the website's reference writes and proves by test (``copy_text.py``: the
change a project's revision file accepts, the entry a pack accepts), so there is one source: this
route calls the same functions, over the rule as the project has it and the word as the project's
Dictionary holds it. A test compares the answer with the generator's own text, and loads the text
the way a person's own file is loaded.

Read-only: nothing is written, and no run is read (the rule is the project's Rulebook, the word
the project's Dictionary).
"""

from __future__ import annotations

from dataclasses import replace
from http import HTTPStatus
from typing import TYPE_CHECKING

from ingence import SCHEMA_VERSION
from ingence.api.routes import dictionary as dictionary_routes
from ingence.api.routes import rules as rule_routes
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, register
from ingence.api.v1.shaping import one
from ingence.copy_text import (
    joined,
    revision_file_name,
    revision_file_text,
    rule_override_text,
    rule_switch_off_text,
    word_entry_text,
)
from ingence.dictionary.inactive import inactivations_in_force
from ingence.rulebook.render import value_in_force

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import Rule

__all__ = ["RuleCopyResponse", "WordCopyResponse"]


@register("rule_copy")
class RuleCopyResponse(ApiModel):
    """``GET /api/v1/rules/copy?rule=<id>``: a rule's changes, as a revision file reads them."""

    schema_version: str
    rule_id: str
    version: str
    #: The change that sets the rule's parameters at the values the project has now, ready to paste
    #: under ``changes:``. None where the rule has no parameter, or is provisional (the Rulebook
    #: refuses a change to a provisional rule).
    values: str | None
    #: The change that switches the rule off; None where the rule is provisional.
    switch_off: str | None
    #: What the card's "Copy as a revision" copies: the rule with its values where it has any,
    #: else the rule switched off; None where neither is possible.
    revision: str | None
    provisional: bool
    #: Whether a newer rule replaced this one. The Rulebook refuses every change to a rule that
    #: is not in force, and a refused file makes all the project's rules unreadable, so a
    #: replaced rule offers no text at all.
    replaced: bool
    #: The whole file that switches the rule off, to save in the project's ``rulebook`` folder:
    #: its number (one past every number the project has taken), its name and its text. None
    #: where the rule offers no switch-off, or where the project's own rules cannot be read.
    next_revision: int | None
    file_name: str | None
    file_text: str | None


class WordCopyEntry(ApiModel):
    """One sense of the word as one origin holds it, as the entry a pack file accepts."""

    #: ``core``, ``domain``, ``laboratory`` or ``yours``, and the section, pack or ``answer``.
    origin: str
    source: str
    text: str


@register("word_copy")
class WordCopyResponse(ApiModel):
    """``GET /api/v1/dictionary/copy?term=<word>``: a word as the entries a pack file accepts."""

    schema_version: str
    term: str
    entries: list[WordCopyEntry]
    #: What the card's "Copy as YAML" copies: every entry, one after another; empty where the
    #: word is inactive.
    text: str


def _declared(rule_id: str) -> Rule | None:
    """The newest shipped declaration of a rule, in force or not (a rule switched off has one)."""
    from ingence.rulebook.load import load_revisions

    found: Rule | None = None
    for revision in sorted(load_revisions(), key=lambda each: each.number):
        for rule in revision.rules:
            if rule.rule_id == rule_id:
                found = rule
    return found


def _replaced(rule_id: str, out_of_force: Mapping[str, str]) -> bool:
    """Whether a newer rule replaced this one, in the project's rules or the engine's own."""
    from ingence.rulebook import shipped_rulebook

    reason = out_of_force.get(rule_id) or dict(shipped_rulebook().inactive).get(rule_id)
    return reason is not None and rule_routes.off_kind(reason) == "replaced"


def _next_revision(project: Path) -> int | None:
    """The number a new revision file of the project's own rules takes, or None where the rules
    already there cannot be read (a file would be numbered against what cannot be seen)."""
    from ingence.rulebook.load import RuleDeclarationError, load_project_revisions
    from ingence.storage.layout import RULEBOOK_DIR
    from ingence.workspace.revisions import next_number

    try:
        return next_number(project, load_project_revisions(project / RULEBOOK_DIR))
    except (RuleDeclarationError, OSError, ValueError):
        return None


@route("rules/copy", model=RuleCopyResponse)
def rule_copy(context: V1Context, query: Mapping[str, Sequence[str]]) -> RuleCopyResponse:
    rule_id = (one(query, "rule") or "").strip()
    if not rule_id:
        message = "name the rule to copy: rule=<name>"
        raise ApiError(message, parameter="rule")
    now, shipped, _, out_of_force = rule_routes.in_force_now(context.project)
    rule = now.get(rule_id) or shipped.get(rule_id) or _declared(rule_id)
    if rule is None:
        message = f"no rule is named {rule_id!r}"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="rule")
    replaced = _replaced(rule.rule_id, out_of_force)
    values = {spec.name: value_in_force(spec) for spec in rule.parameters}
    switched_off = None if replaced else rule_switch_off_text(rule)
    with_values = None if replaced else rule_override_text(rule, values)
    number = _next_revision(context.project) if switched_off is not None else None
    return RuleCopyResponse(
        schema_version=SCHEMA_VERSION,
        rule_id=rule.rule_id,
        version=rule.version,
        values=with_values,
        switch_off=switched_off,
        revision=with_values if with_values is not None else switched_off,
        provisional=rule.status.value == "provisional",
        replaced=replaced,
        next_revision=number,
        file_name=revision_file_name(number) if number is not None else None,
        file_text=(
            revision_file_text(number, [switched_off], summary=f"Switch off {rule.rule_id}.")
            if number is not None and switched_off is not None
            else None
        ),
    )


@route("dictionary/copy", model=WordCopyResponse)
def word_copy(context: V1Context, query: Mapping[str, Sequence[str]]) -> WordCopyResponse:
    term = (one(query, "term") or "").strip()
    if not term:
        message = "name the word to copy: term=<word>"
        raise ApiError(message, parameter="term")
    held = [
        (placed, sense)
        for placed, sense in dictionary_routes.composed_senses(context.project, fold=True)
        if placed.term == term
    ]
    if not held:
        message = f"no word {term!r} is in this project's Dictionary"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="term")
    # What a person inactivated is not copied: an inactive word offers nothing, and an inactive
    # spelling is left out of its word's entry (a pack cannot inactivate, so copying it would
    # bring back what the person put away).
    inactive = inactivations_in_force(context.project)
    entries: list[WordCopyEntry] = []
    for placed, sense in held if inactive.sense(term) is None else []:
        kept = tuple(
            spelling for spelling in sense.surfaces if inactive.surface(term, spelling) is None
        )
        if kept:
            entries.append(
                WordCopyEntry(
                    origin=placed.origin,
                    source=placed.source,
                    text=word_entry_text(replace(sense, surfaces=kept)),
                )
            )
    return WordCopyResponse(
        schema_version=SCHEMA_VERSION,
        term=term,
        entries=entries,
        text=joined([entry.text for entry in entries]),
    )
