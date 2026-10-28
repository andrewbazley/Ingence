"""The interpretive rules. Revisions are append-only.

The code is the interpreter, and the rules are the product. A rule's English sentence and its
executable form are two renderings of one object. They are not two objects kept in sync.

- ``load`` reads a shipped or project revision file, and refuses what is not one.
- ``resolve`` composes revisions into an effective Rulebook and its hash.
- ``registry`` binds predicates to declarations.
- ``render`` fills the English template from the parameters in force.
- ``predicates`` holds the code that decides each rule, one module per topic.
- ``builtin/`` holds the shipped revisions, one file each.

A project's own revisions live in its ``rulebook`` directory. They are numbered in a sequence of
their own and composed after the shipped Rulebook.

The rule record lives in :mod:`ingence.core.schema` beside every other record type, and not in a
``schema`` module here. One schema module lets the exported JSON Schema be generated from one place.
A second module would be a second place for a record type to be defined.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from ingence.rulebook.render import value_in_force

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ingence.rulebook.load import ProjectRevision
    from ingence.rulebook.resolve import EffectiveRulebook

__all__ = ["AREA_RULE_ID", "bound_from_settings", "project_rulebook", "shipped_rulebook"]


@functools.cache
def shipped_rulebook() -> EffectiveRulebook:
    """Return the rules in force at the latest shipped revision.

    Cached, because a run asks for it once per run record and the answer cannot
    change inside a process: the revision files are package data and the loader
    is a pure function of them. Deliberately not cached across processes, so a
    person editing a rule sees the change on the next run rather than on the next
    reinstall.

    Importing the predicates here rather than at module scope keeps the registry's
    one rule intact: nothing reaches a predicate except through the registry, and
    a package that imported every predicate to be importable itself would have
    reintroduced the coupling the registry exists to prevent.
    """
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook

    load_builtins()
    return effective_rulebook(load_revisions())


def project_rulebook(project_path: Path) -> EffectiveRulebook:
    """Return the rules in force for one project: the shipped Rulebook, then the project's own.

    Not cached, because a project's revision files are the user's to add to between runs and the
    answer is expected to change. A project with no revisions of its own has exactly the shipped
    Rulebook, digest included.
    """
    from ingence.rulebook.load import load_project_revisions, load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook
    from ingence.storage.layout import RULEBOOK_DIR

    load_builtins()
    return effective_rulebook(
        load_revisions(),
        project=bound_from_settings(
            project_path, load_project_revisions(project_path / RULEBOOK_DIR)
        ),
    )


#: The rule whose ``area_level`` is bound from ``themes.area_level``.
AREA_RULE_ID = "area_kind_from_folder_name"


def bound_from_settings(
    project_path: Path, revisions: Sequence[ProjectRevision]
) -> tuple[ProjectRevision, ...]:
    """The project's revisions, and one more where a setting binds a rule's parameter.

    One setting governs both: ``themes.area_level`` says which folder below a root is an area, and
    the area rule reads its ``area_level``; where the setting differs from the rule's declared
    default, an override composes after the project's own revisions through the one path a project
    changes a parameter by (``ProjectChange``, verb ``override``), numbered after them, so the run's
    Rulebook digest says the level it ran at. A project whose settings cannot be read binds nothing.
    """
    from ingence.rulebook.load import ChangeVerb, ProjectChange, ProjectRevision

    try:
        from ingence.core.project import Project

        level = Project.open(project_path).settings.themes.area_level
    except Exception:
        return tuple(revisions)
    declared = shipped_rulebook().by_id(AREA_RULE_ID)
    if declared is None:
        return tuple(revisions)
    default = next(
        (
            value_in_force(specification)
            for specification in declared.parameters
            if specification.name == "area_level"
        ),
        None,
    )
    if level is None or level == default:
        return tuple(revisions)
    number = max((revision.number for revision in revisions), default=0) + 1
    bound = ProjectRevision(
        number=number,
        summary="the area level, bound from the setting themes.area_level",
        changes=(
            ProjectChange(
                verb=ChangeVerb.OVERRIDE, rule_id=AREA_RULE_ID, parameters={"area_level": level}
            ),
        ),
    )
    return (*revisions, bound)
