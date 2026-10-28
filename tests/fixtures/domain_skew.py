"""Ask every tie between domains, however few files carry the word.

A word two domains spell differently, in a folder whose files lean neither way, is a question only
where more than ``dictionary.domain_skew.question_above_files`` files carry it; a small test pool
writes a tied word in a few names and expects the question, so it sets the floor to zero.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ingence.core.project import Project

__all__ = ["ask_every_tie"]


def ask_every_tie(project: Path) -> Project:
    """Set ``dictionary.domain_skew.question_above_files`` to 0 in a project's settings.

    The function returns the project opened again, so that a run reads the setting.
    """
    settings = project / "settings.yaml"
    held = yaml.safe_load(settings.read_text(encoding="utf-8")) or {}
    held.setdefault("dictionary", {}).setdefault("domain_skew", {})["question_above_files"] = 0
    settings.write_text(yaml.safe_dump(held), encoding="utf-8")
    return Project.open(project)
