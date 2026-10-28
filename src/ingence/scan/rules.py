"""The scan layer's own rules, bound from the Rulebook at the start of a run.

Four rules establish what the pool holds before anything is inferred. A directory contains
what enumeration listed inside it. A directory holds a total of what is beneath it. A
non-file object has a kind. A container embeds what opening it recovered. The Rulebook
declares all four, as provisional, under the identifiers every run cites. So a reader can open
and read the rule behind the most numerous records in any run.

A run binds each rule once and calls it where the fact is computed. It takes each rule's
version and predicate from the Rulebook, through the registry like any other. The census and
the cascade call the predicate and read no constant.

A run uses the shipped Rulebook unless it is handed another. While the four rules are
provisional, no project revision may switch them off, supersede them or tune them. A
project's composition therefore holds them exactly as shipped. Reading the shipped Rulebook
spares ``index`` and ``dive`` from failing on a project revision that nothing in them reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ingence.rulebook.registry import Predicate
    from ingence.rulebook.resolve import EffectiveRulebook

__all__ = [
    "CASCADE_MEMBERSHIP",
    "CENSUS_AGGREGATE",
    "CENSUS_CONTAINMENT",
    "CENSUS_OBJECT_KIND",
    "CENSUS_RULE_IDS",
    "ScanRule",
    "scan_rules",
]

#: A directory contains the entries enumeration listed directly inside it.
CENSUS_CONTAINMENT = "census.filesystem_containment"
#: A directory holds exactly what enumeration found, directly and beneath it.
CENSUS_AGGREGATE = "census.directory_aggregate"
#: The precise kind of an object that is not a file, a directory or a link.
CENSUS_OBJECT_KIND = "census.object_kind"
#: A container embeds each member opening it recovered.
CASCADE_MEMBERSHIP = "cascade.container_member"

#: Every rule the census runs.
CENSUS_RULE_IDS: tuple[str, ...] = (CENSUS_CONTAINMENT, CENSUS_AGGREGATE, CENSUS_OBJECT_KIND)


@dataclass(frozen=True, slots=True)
class ScanRule:
    """One of the scan layer's rules, bound to its predicate at the values in force."""

    rule_id: str
    version: str
    predicate: Predicate
    parameters: Mapping[str, object] = field(default_factory=dict)

    def evaluate(self, subject: object) -> object:
        """Return the predicate's verdict on what the scan layer found."""
        return self.predicate.evaluate(subject, self.parameters)


def scan_rules(
    identifiers: Iterable[str], rulebook: EffectiveRulebook | None = None
) -> dict[str, ScanRule]:
    """Bind the scan layer's rules from a Rulebook, the shipped one where none is given.

    The function refuses a rule that is not in force, or that does not enumerate, and does not
    fall back to a version written here. A record that cites a rule the Rulebook does not hold
    breaks the claim that every record is attributable to a rule.
    """
    from ingence.core.schema import RuleEffect
    from ingence.rulebook import shipped_rulebook
    from ingence.rulebook.load import RuleDeclarationError
    from ingence.rulebook.registry import PREDICATES
    from ingence.rulebook.render import value_in_force

    book = shipped_rulebook() if rulebook is None else rulebook
    bound: dict[str, ScanRule] = {}
    for identifier in identifiers:
        rule = book.by_id(identifier)
        if rule is None:
            message = (
                f"the Rulebook in force holds no rule {identifier!r}, and the scan layer "
                f"draws its records under it"
            )
            raise RuleDeclarationError(message)
        if rule.effect is not RuleEffect.ENUMERATES:
            message = (
                f"rule {identifier!r} is {rule.effect.value}, and the scan layer runs only "
                f"rules that enumerate"
            )
            raise RuleDeclarationError(message)
        bound[identifier] = ScanRule(
            rule_id=rule.rule_id,
            version=rule.version,
            predicate=PREDICATES.get(rule.predicate),
            parameters={
                specification.name: value_in_force(specification)
                for specification in rule.parameters
            },
        )
    return bound
