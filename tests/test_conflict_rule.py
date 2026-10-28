"""The conflict rule is declared again at version 2 on predicate version 3, naming the kept tie."""

from __future__ import annotations

from ingence.rulebook import shipped_rulebook
from ingence.rulebook.revision_5.names import CHANGES


def test_the_conflict_rule_is_superseded_and_declared_again_naming_the_kept_tie() -> None:
    assert {"supersede": "binding_is_ambiguous_outside_programs"} in CHANGES
    assert shipped_rulebook().by_id("binding_is_ambiguous_outside_programs") is None
    rule = shipped_rulebook().by_id("binding_is_ambiguous_outside_programs_and_domains")
    assert rule is not None
    assert (rule.version, rule.predicate, rule.predicate_version) == (
        "1",
        "readings_conflict",
        "3",
    )
    assert "kept as a tie and not raised" in rule.statement
    assert {parameter.name for parameter in rule.parameters} == {"reading", "minimum_alternatives"}
