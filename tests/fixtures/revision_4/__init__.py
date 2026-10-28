"""Revision 4 of the shipped rules, and the retired file that projects once held them in.

The rules are revision 4 of the shipped book (``ingence.rulebook.revision_4``), so a test that
wants them in force writes nothing. :func:`write_revision` writes no file and returns where the
retired file would stand. A test of a project's own rules writes what a person's rule change
writes, a ``project-NNNN.yaml`` of overrides (:func:`tune`, :func:`each_deviation`). A test of
the retired file writes it as ``init`` wrote it before the rules joined the shipped book
(:func:`write_retired`, :func:`retired_form`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ingence.rulebook.retired import FILE_NAME
from ingence.rulebook.revision_4 import REVISION
from ingence.storage.layout import RULEBOOK_DIR

__all__ = [
    "FILE_NAME",
    "REVISION",
    "each_deviation",
    "retired_form",
    "stored_version",
    "tune",
    "write_retired",
    "write_revision",
]


def write_revision(project_path: Path) -> Path:
    """Return where the retired file would stand, which :func:`each_deviation` takes.

    There is nothing to write, because the rules are in force in every project.
    """
    return project_path / RULEBOOK_DIR / FILE_NAME


def tune(project_path: Path, parameter: str, value: object, *, rules: tuple[str, ...] = ()) -> int:
    """Set ``parameter`` on every rule in force that declares it, or on ``rules`` only.

    The change is written as a person's rule change is written: an override revision. The
    function returns the revision's number.
    """
    from ingence.rulebook import project_rulebook
    from ingence.workspace.revisions import write_revision as write_rule_change

    book = project_rulebook(project_path)
    chosen = {
        rule.rule_id: {parameter: value}
        for rule in book.rules
        if (not rules or rule.rule_id in rules)
        and any(spec.name == parameter for spec in rule.parameters)
    }
    assert chosen, f"no rule in force declares {parameter!r}"
    return write_rule_change(project_path, chosen, summary=f"{parameter}: {value}")


def each_deviation(path: Path) -> Path:
    """Set ``derivations`` to ``each_deviation`` in the project whose rulebook ``path`` names.

    The rule's default is ``lattice``. With ``each_deviation``, every word that an experiment adds
    to an approved core, or lacks from it, is its own question.
    """
    tune(path.parent.parent, "derivations", "each_deviation")
    return path


#: Where the past versions of the retired file are stored, with their words removed.
STORED = Path(__file__).resolve().parent.parent / "retired_rules"


def stored_version(day: str) -> dict[str, Any]:
    """The retired file as it stood on ``day`` (``2026-09-15``, ``2026-09-28``, ``2026-09-30``)."""
    document = yaml.safe_load((STORED / f"as-written-{day}.yaml").read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def retired_form() -> dict[str, Any]:
    """The retired file in the last form that ``init`` wrote into a project.

    The file is stored as written and never rebuilt from today's rules. A rule that a later
    revision renames would otherwise turn up in the file under its new name.
    """
    return stored_version("2026-09-30")


#: What a stored version says in place of its words, which were removed before storing it.
_NO_WORDS = "Stored without its words."


def _worded(node: Any) -> Any:
    """A stored version with placeholder words, so it loads as a project revision does."""
    if isinstance(node, list):
        return [_worded(item) for item in node]
    if not isinstance(node, dict):
        return node
    filled = {key: _worded(value) for key, value in node.items()}
    if "predicate" in filled and "statement" not in filled:
        slots = ", ".join(f"{{{item['name']}}}" for item in filled.get("parameters") or ())
        filled["statement"] = f"A rule stored without its words: {slots}." if slots else _NO_WORDS
    if ("predicate" in filled or "scope" in filled) and "rationale" not in filled:
        filled["rationale"] = _NO_WORDS
    if "type" in filled and "name" in filled and "description" not in filled:
        filled["description"] = _NO_WORDS
    if "project_revision" in filled and "summary" not in filled:
        filled["summary"] = _NO_WORDS
    return filled


def write_retired(project_path: Path, document: object | None = None) -> Path:
    """Write the retired file into a project's rulebook folder, as ``init`` did, and return it."""
    directory = project_path / RULEBOOK_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / FILE_NAME
    body = _worded(retired_form() if document is None else document)
    path.write_text(yaml.safe_dump(body, sort_keys=False, width=100), encoding="utf-8")
    return path
