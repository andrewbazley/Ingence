"""The rules in force, each led by a sentence in plain words.

Each rule of the run being read is shown by the sentence that ``api/plain_words.yaml`` holds for
it. The rule's own statement, filled in at the values the run used, is one click away. The
statement is what a run records and what a person approves, and it is hashed into the Rulebook's
digest, so the page never rewrites it. A rule that the table holds no sentence for leads with its
statement instead, and never with nothing.

Beside each rule is how much rests on it in this run: its conclusions, its flags and the pairs it
suggested, counted from the run's own tables. The rules are grouped by what a rule does, in the
order the engine applies them, each group under a plain heading.

Every parameter is shown as an input that a person can type into. These are each rule's declared
parameters, by their ``ParameterSpec`` (its type, its choices and its bounds). The page validates
against the spec before a draft is saved, and the Rulebook checks again. Beside each input are the
value this run used, the value that the project's own revisions give now (a commit not yet
re-run), the shipped default, and any draft of it with the Rulebook's refusal. A typed value is a
draft of kind ``rule_parameters``. It is committed as a project Rulebook revision through the
workspace (``workspace/revisions.py``), and never to ``settings.yaml`` or the Record.

An inactive rule is listed apart: a rule that the project's revisions switch off or supersede
now, with the reason the Rulebook gives, whether or not the run being read still used it.

Nothing here writes, and nothing here interprets. Every figure is a count over the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ingence.api.plain_words import rule_sentence
from ingence.core.schema import ParameterSpec, Rule
from ingence.rulebook.describe import decides, reads
from ingence.rulebook.render import plain_reason, render_rule, value_in_force
from ingence.storage.index_store import record_from_row

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = ["HEADINGS", "in_force_now", "off_kind", "parameters_of", "rules"]

#: What each kind of rule does, as a heading a person reads, in the order the engine applies
#: them. A kind not named here is listed after these under its own name.
HEADINGS: dict[str, str] = {
    "enumerates": "What the walk itself records",
    "classifies": "What files are",
    "groups": "Which files go together",
    "proposes": "Pairs suggested for a look",
    "flags": "What is raised for attention",
    "constraint": "Limits every conclusion keeps to",
}


def off_kind(reason: str) -> str:
    """Who switched a rule off, from the Rulebook's own reason for it.

    ``person``: a revision of the project's own rules switched it off. ``replaced``: a newer rule
    stands in its place (the engine's own, or the project's). ``shipped``: the engine ships it
    switched off, and no one in this project has turned it on.
    """
    if reason.startswith("disabled at project revision"):
        return "person"
    if reason.startswith("superseded"):
        return "replaced"
    return "shipped"


def _counted(catalog: Catalog, table: str) -> dict[str, int]:
    """How many rows of one table each rule produced. The table is a literal, never a request's."""
    try:
        found = catalog.query(
            f"SELECT rule_id, count(*) FROM {table} GROUP BY rule_id"  # noqa: S608
        ).fetchall()
    except Exception:  # a table a run never wrote: nothing rests on any rule there
        return {}
    return {str(rule_id): int(count) for rule_id, count in found}


def in_force_now(
    project: Path | None,
) -> tuple[dict[str, Rule], dict[str, Rule], str | None, dict[str, str]]:
    """The rules the project's revisions give now, the shipped ones, and those out of force.

    A commit of rule parameters writes a project revision that the next run reads. So what a
    parameter is now can differ from what this run used. Where the revisions cannot be composed,
    the function returns the reason, and the page shows the run's values alone and never guesses.
    The last part of the result is each rule out of force now (switched off or superseded), with
    the Rulebook's reason.
    """
    if project is None:
        return {}, {}, None, {}
    from ingence.rulebook.load import RuleDeclarationError, load_project_revisions, load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook
    from ingence.storage.layout import RULEBOOK_DIR

    try:
        load_builtins()
        shipped = load_revisions()
        now = effective_rulebook(shipped, project=load_project_revisions(project / RULEBOOK_DIR))
        first = effective_rulebook(shipped)
    except (RuleDeclarationError, OSError, ValueError) as error:
        return {}, {}, str(error), {}
    return (
        {rule.rule_id: rule for rule in now.rules},
        {rule.rule_id: rule for rule in first.rules},
        None,
        dict(now.inactive),
    )


def parameters_of(
    rule: Rule,
    *,
    now: Rule | None = None,
    shipped: Rule | None = None,
    drafted: dict[str, Any] | None = None,
    refused: str | None = None,
) -> list[dict[str, Any]]:
    """Each parameter of one rule as the page's input shows it, in declared order.

    ``value`` is what this run used. ``committed`` is what the project's revisions give now (the
    same unless a commit has not been re-run). ``default`` is the shipped value. ``draft`` is the
    typed value that is not yet committed, where there is one, with the Rulebook's ``error``
    beside it.
    """

    def spec_of(held: Rule | None, name: str) -> ParameterSpec | None:
        if held is None:
            return None
        return next((item for item in held.parameters if item.name == name), None)

    shown: list[dict[str, Any]] = []
    for specification in rule.parameters:
        committed_spec = spec_of(now, specification.name)
        shipped_spec = spec_of(shipped, specification.name)
        used = value_in_force(specification)
        committed = value_in_force(committed_spec) if committed_spec is not None else used
        in_draft = drafted is not None and specification.name in drafted
        shown.append(
            {
                "name": specification.name,
                "type": specification.type.value,
                "choices": list(specification.choices),
                "minimum": specification.minimum,
                "maximum": specification.maximum,
                "description": specification.description,
                "value": used,
                "committed": committed,
                "default": value_in_force(shipped_spec) if shipped_spec is not None else None,
                "drafted": in_draft,
                "draft": drafted.get(specification.name) if in_draft and drafted else None,
                "error": refused if in_draft else None,
            }
        )
    return shown


def rules(catalog: Catalog, project: Path | None = None) -> dict[str, Any]:
    """Every rule the run carries, each with its plain sentence, its statement and its counts.

    Given the project, each rule also carries its parameters as inputs (:func:`parameters_of`).
    """
    if not catalog.has_run:
        return {"sections": [], "rules": 0}
    now, shipped, unreadable, out_of_force = in_force_now(project)
    drafts: dict[str, Any] = {}
    draft_errors: dict[str, str] = {}
    if project is not None:
        from ingence.workspace.drafts import read_draft

        held = read_draft(project, "rule_parameters")
        drafts = held.values
        draft_errors = held.errors
    rows = catalog.query("SELECT * FROM rules ORDER BY rule_id, version").to_arrow_table()
    from ingence.rulebook.availability import NOT_AVAILABLE, unavailable_rules

    unavailable = unavailable_rules(
        record_from_row(row, Rule) for row in rows.to_pylist() if row.get("enabled", True)
    )
    claims = _counted(catalog, "claims")
    findings = _counted(catalog, "findings")
    candidates = _counted(catalog, "candidates")
    by_effect: dict[str, list[dict[str, Any]]] = {}
    inactive: list[dict[str, Any]] = []
    for row in rows.to_pylist():
        rule = record_from_row(row, Rule)
        statement = render_rule(rule)
        plain = rule_sentence(rule.rule_id)
        rests = {
            "claims": claims.get(rule.rule_id, 0),
            "findings": findings.get(rule.rule_id, 0),
            "candidates": candidates.get(rule.rule_id, 0),
        }
        shown = {
            "rule_id": rule.rule_id,
            "version": rule.version,
            "effect": rule.effect.value,
            "enabled": bool(row.get("enabled", True)),
            "plain": (
                f"{NOT_AVAILABLE} {plain or statement}"
                if rule.rule_id in unavailable
                else plain or statement
            ),
            "plain_from": "plain words" if plain else "statement",
            "statement": statement,
            "heading": HEADINGS.get(rule.effect.value, rule.effect.value.replace("_", " ")),
            "decides": decides(rule),
            "reads": reads(rule),
            "defeated_by": list(rule.defeated_by),
            **rests,
            "rests_on": sum(rests.values()),
            "parameters": parameters_of(
                rule,
                now=now.get(rule.rule_id),
                shipped=shipped.get(rule.rule_id),
                drafted=drafts.get(rule.rule_id)
                if isinstance(drafts.get(rule.rule_id), dict)
                else None,
                refused=draft_errors.get(rule.rule_id),
            ),
        }
        reason = out_of_force.get(rule.rule_id) or (None if shown["enabled"] else "disabled")
        if reason:
            inactive.append(
                {
                    **shown,
                    "reason": plain_reason(reason),
                    "off_kind": off_kind(reason),
                    "in_this_run": True,
                }
            )
        else:
            by_effect.setdefault(rule.effect.value, []).append(shown)
    inactive.extend(_not_in_this_run(out_of_force, {item["rule_id"] for item in inactive}))
    order = [*HEADINGS, *sorted(set(by_effect) - set(HEADINGS))]
    sections: list[dict[str, Any]] = [
        {
            "effect": effect,
            "heading": HEADINGS.get(effect, effect.replace("_", " ")),
            "rules": sorted(
                by_effect[effect], key=lambda item: (-int(item["rests_on"]), item["rule_id"])
            ),
        }
        for effect in order
        if effect in by_effect
    ]
    return {
        "sections": sections,
        "rules": sum(len(section["rules"]) for section in sections),
        "run": _run_named(catalog),
        "inactive": sorted(inactive, key=lambda item: str(item["rule_id"])),
        "revisions_unreadable": unreadable,
        "older_rules_file": _older_rules_file(project),
    }


def _run_named(catalog: Catalog) -> dict[str, Any] | None:
    """The run these rules were counted in, named as a person reads it ("Run 3, ..."), or None."""
    from ingence.api.run_names import project_run_names

    if catalog.run_directory is None:
        return None
    return project_run_names(catalog.project).get(catalog.run_directory.name)


def _older_rules_file(project: Path | None) -> str | None:
    """What the Rules page says, once, where the project holds the retired rules file."""
    if project is None:
        return None
    from ingence.rulebook.retired import read_retired, sentence
    from ingence.storage.layout import RULEBOOK_DIR

    found = read_retired(project / RULEBOOK_DIR)
    return None if found is None else f"{sentence(found)}."


def _not_in_this_run(out_of_force: dict[str, str], listed: set[str]) -> list[dict[str, Any]]:
    """Rules out of force now that the run did not carry either, from their declarations."""
    if not set(out_of_force) - listed:
        return []
    from ingence.rulebook.load import load_revisions

    declared: dict[str, Rule] = {}
    for revision in load_revisions():
        for each in revision.rules:
            declared[each.rule_id] = each
    shown: list[dict[str, Any]] = []
    for rule_id in sorted(set(out_of_force) - listed):
        rule = declared.get(rule_id)
        statement = render_rule(rule) if rule is not None else ""
        effect = rule.effect.value if rule is not None else ""
        shown.append(
            {
                "rule_id": rule_id,
                "version": rule.version if rule is not None else None,
                "effect": effect,
                "heading": HEADINGS.get(effect, effect.replace("_", " ")),
                "plain": rule_sentence(rule_id) or statement or rule_id,
                "statement": statement,
                "decides": decides(rule) if rule is not None else "",
                "reads": reads(rule) if rule is not None else "",
                "defeated_by": list(rule.defeated_by) if rule is not None else [],
                "parameters": [],
                "reason": plain_reason(out_of_force[rule_id]),
                "off_kind": off_kind(out_of_force[rule_id]),
                "in_this_run": False,
            }
        )
    return shown
