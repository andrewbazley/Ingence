"""A commit's rule parameters, as a new project Rulebook revision (append-only).

A project's own revisions are files that the Rulebook already composes after the shipped one. A
parameter that a person tunes is an ``override`` change in a revision of its own, so the history of
every parameter is the revision files, and no revision is ever edited. This module adds only the
check before the write. The Rulebook can refuse a revision (an unknown parameter, a value that the
parameter's own bounds refuse, a provisional rule), and a refused revision would stop every later
open of the project. So each override is composed against the revisions in force first. One that
fails is reported beside its rule, and nothing is written.

One test decides what counts as a rule's change, and the check, the writer and the commit all use
it (:func:`overrides_of`): a non-empty mapping of parameter names to values. If the check passed
over a value that was not a mapping while the writer still wrote it, a list of two-letter words
would become parameter names, and the Rulebook would then refuse to load.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from ingence.rulebook.load import ProjectRevision

__all__ = ["check_overrides", "next_number", "overrides_of", "write_revision"]


def overrides_of(draft: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    """The rules a draft of rule parameters changes, each with its parameters, by rule.

    Only a non-empty mapping of parameter names to values is a change; an empty one changes
    nothing, and anything else is refused by :func:`check_overrides` before it can be written.
    """
    return {
        rule_id: dict(parameters)
        for rule_id, parameters in sorted(draft.items())
        if isinstance(parameters, dict) and parameters
    }


def next_number(project: Path, existing: Iterable[ProjectRevision]) -> int:
    """The number a new project revision takes: one past every number taken.

    The retired rules file's number stays taken while the file stands or has been set aside,
    whether or not anything of it still applies. A run made before the file was retired read it as
    project revision 1, and a commit taking that number would make a restore of the run read the
    commit as part of what the run used.
    """
    from ingence.rulebook.retired import taken
    from ingence.storage.layout import RULEBOOK_DIR

    floor = 1 if taken(project / RULEBOOK_DIR) else 0
    return max([floor, *(revision.number for revision in existing)]) + 1


def check_overrides(project: Path, overrides: Mapping[str, object]) -> dict[str, str]:
    """Each rule whose override the Rulebook would refuse, with the reason in its own words."""
    from ingence.rulebook.load import (
        ChangeVerb,
        ProjectChange,
        ProjectRevision,
        RuleDeclarationError,
        load_project_revisions,
        load_revisions,
    )
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook
    from ingence.storage.layout import RULEBOOK_DIR

    load_builtins()
    shipped = load_revisions()
    existing = load_project_revisions(project / RULEBOOK_DIR)
    number = next_number(project, existing)
    refused: dict[str, str] = {
        rule_id: "a rule's parameters are given as names, each with its value"
        for rule_id, parameters in overrides.items()
        if not isinstance(parameters, dict)
    }
    for rule_id, parameters in overrides_of(overrides).items():
        candidate = ProjectRevision(
            number=number,
            summary="check",
            changes=(ProjectChange(ChangeVerb.OVERRIDE, rule_id, parameters),),
        )
        try:
            effective_rulebook(shipped, project=(*existing, candidate))
        except RuleDeclarationError as error:
            refused[rule_id] = str(error)
    return refused


def write_revision(project: Path, overrides: Mapping[str, object], *, summary: str) -> int:
    """Append a project revision of ``override`` changes; return its number.

    Refuses (``ValueError``, nothing written) where :func:`check_overrides` finds any.
    """
    from ingence.rulebook.load import load_project_revisions
    from ingence.storage.layout import RULEBOOK_DIR

    refused = check_overrides(project, overrides)
    if refused:
        message = "; ".join(f"{rule_id}: {why}" for rule_id, why in refused.items())
        raise ValueError(message)
    directory = project / RULEBOOK_DIR
    directory.mkdir(parents=True, exist_ok=True)
    number = next_number(project, load_project_revisions(directory))
    document = {
        "project_revision": number,
        "summary": summary,
        "changes": [
            {"override": rule_id, "parameters": parameters}
            for rule_id, parameters in overrides_of(overrides).items()
        ],
    }
    path = directory / f"project-{number:04d}.yaml"
    # Created exclusively, so two commits can never take one number.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, sort_keys=False)
    return number
