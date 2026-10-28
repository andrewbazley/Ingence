"""Revision 5 ships with the 0.4.0 contract: it loads, numbers itself 5, composes after
revision 4, and a module declares its rules there. Every name is invented.
"""

from __future__ import annotations

from ingence.rulebook import shipped_rulebook
from ingence.rulebook.load import load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.resolve import effective_rulebook
from ingence.rulebook.revision_5 import REVISION


def test_revision_5_loads_and_numbers_itself_5() -> None:
    assert REVISION["revision"] == 5
    assert set(REVISION) == {"revision", "summary", "rules", "changes"}
    load_builtins()
    revisions = load_revisions()
    assert [revision.number for revision in revisions][-2:] == [4, 5]
    fifth = revisions[-1]
    assert fifth.number == 5 and fifth.summary
    assert {rule.rule_id for rule in fifth.rules} == {
        rule["id"]
        for rule in REVISION["rules"]  # type: ignore[union-attr]
    }


def test_revision_5_composes_after_revision_4_and_keeps_every_rule_it_does_not_supersede() -> None:
    """Every rule of revision 4 that revision 5 does not supersede is still in force beside
    revision 5's own, and the shipped Rulebook is the whole."""
    load_builtins()
    revisions = load_revisions()
    at_four = effective_rulebook(revisions, at=4)
    whole = effective_rulebook(revisions)
    assert whole.revision == 5 and at_four.revision == 4
    superseded = {str(change.get("supersede")) for change in REVISION["changes"]}  # type: ignore[union-attr]
    in_force = {rule.rule_id for rule in whole.rules}
    assert {rule.rule_id for rule in at_four.rules} - superseded <= in_force
    assert {rule["id"] for rule in REVISION["rules"]} <= in_force  # type: ignore[index]
    assert shipped_rulebook().digest == whole.digest
    if REVISION["rules"] or REVISION["changes"]:
        assert whole.digest != at_four.digest
    else:
        assert whole.digest == at_four.digest


def test_each_revision_5_module_declares_its_lists_and_has_a_docstring() -> None:
    """Each revision 5 module declares ``RULES`` and ``CHANGES`` as lists, and has a docstring."""
    from ingence.rulebook.revision_5 import areas, file_types, names, reading, readouts, workflows

    for module in (reading, file_types, names, workflows, areas, readouts):
        assert isinstance(module.RULES, list) and isinstance(module.CHANGES, list), module.__name__
        assert (module.__doc__ or "").strip(), module.__name__
