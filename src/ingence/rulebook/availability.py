"""Which shipped rules cannot run in this version, from the engine's own facts.

A rule is not available in this version when either of these holds. Each is a fact that the engine
states in its own code:

- The rule proposes pairs and names no set of files to draw them from, so it proposes nothing.
- The predicate that decides the rule is declared, in the words of its own description, as declared
  and not evaluable (or says it is not evaluable), and it refuses when asked at the rule's own
  shipped values. Both are needed. A description alone can be about only one setting of a rule, and
  a refusal alone can be a refusal of the wrong kind of subject.

The rule texts do not say this. The module adds it where a rule is shown. ``ingence rule show``, the
Rules page and the generated reference all read it from here, so the three agree. A rule whose
predicate gains an evaluator stops saying it the moment the evaluator exists. ``ingence infer``
counts the same rules, leaving out the constraint rules, which it never evaluates as rules of their
own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ingence.core.schema import Rule

__all__ = ["NOT_AVAILABLE", "say_unavailable", "unavailable_rules"]

#: The sentence a person reads beside a rule that cannot run yet.
NOT_AVAILABLE = "Not available in this version."

_DECLARED_ONLY = "Declared and not evaluable"


def unavailable_rules(rules: Iterable[Rule]) -> dict[str, str]:
    """Return each rule that cannot run in this version, with one sentence saying why not."""
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.registry import PREDICATES, PredicateNotEvaluableError
    from ingence.rulebook.render import value_in_force

    load_builtins()
    found: dict[str, str] = {}
    for rule in rules:
        if rule.effect.value == "proposes" and rule.candidates_within is None:
            found[rule.rule_id] = (
                "It proposes pairs but names no set of files to draw them from, so it "
                "proposes nothing yet."
            )
            continue
        try:
            predicate = PREDICATES.get(rule.predicate)
        except Exception:  # noqa: S112 -- a predicate this version does not register cannot be asked
            continue
        words = (type(predicate).__doc__ or "").lower()
        says_so = "not evaluable" in words
        refuses = False
        if says_so:
            values = {spec.name: value_in_force(spec) for spec in rule.parameters}
            try:
                predicate.evaluate(None, values)
            except PredicateNotEvaluableError:
                refuses = True
            except Exception:  # any other failure means the rule reads its subject
                refuses = False
        if _DECLARED_ONLY.lower() in words or (says_so and refuses):
            found[rule.rule_id] = (
                "Its test is declared and not written yet, so it refuses nothing."
                if rule.effect.value == "constraint"
                else "Its test is declared and not written yet, so the engine skips it and "
                "it concludes nothing."
            )
    return found


def say_unavailable(sentences: Mapping[str, str], unavailable: Iterable[str]) -> dict[str, str]:
    """Return ``sentences`` with the availability sentence leading each unavailable rule's."""
    names = set(unavailable)
    return {
        rule_id: f"{NOT_AVAILABLE} {sentence}" if rule_id in names else sentence
        for rule_id, sentence in sentences.items()
    }
