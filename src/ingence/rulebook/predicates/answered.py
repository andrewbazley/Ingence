"""A set of copies a person answered, keyed by the set.

A person's "Copies" stands as their answer, as an approved group of experiments does
(``experiments.ApprovedExperimentGroup``). At the run's Record watermark, the pass reads the
classes of copies that a person answered are copies of one file. It hands each file of such a
class the set it belongs to (``record/answers.py``). This predicate returns that set as the class
key. So the files are grouped as the person said, beside what the rules that form copies
concluded. With no answer there is no key, and nothing is grouped by a rule's reading alone.

The rule that reads the key is also what a person's "Unrelated" is recorded as. The defeater on a
copy claim that a person refused names this rule, so the retraction opens a rule that a person
can read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.schema import RuleEffect
from ingence.rulebook.registry import Abstention, ClassKey, Reach, Reads, declare

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.rulebook.registry import Predicate

__all__ = [
    "ANSWERED_CLAIM_CLASS_PREDICATE",
    "ANSWERED_COPIES_PREDICATE",
    "AnsweredClaimClass",
    "AnsweredCopiesSet",
]

#: The predicate's name, which the pass and the pages recognize a person's copies answer by.
ANSWERED_COPIES_PREDICATE = "answered_copies"


class AnsweredCopiesSet:
    """Files a person answered are copies of one file, keyed by the set they answered."""

    name = ANSWERED_COPIES_PREDICATE
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the answered set as the class key, or None where no answer holds the entry."""
        del parameters
        copies = getattr(subject, "copies", None)
        entry = getattr(subject, "entry", None)
        if not copies or entry is None:
            return None
        return ClassKey(key=str(copies), inputs=(entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no person answered a set holding the entry is copies."""
        del subject, parameters
        return Abstention(reason="no set of copies a person answered holds it")


@declare(
    ANSWERED_COPIES_PREDICATE,
    version="1",
    summary="Group the files of each set a person answered are copies of one file",
    effect=RuleEffect.GROUPS,
    reads=Reads.ANSWERED_COPIES,
    reach=Reach.ATTACHED,
)
def _build_answered_copies() -> Predicate:
    return AnsweredCopiesSet()


#: The predicate that a person's answer on a class of claims is recorded as.
ANSWERED_CLAIM_CLASS_PREDICATE = "answered_claim_class"


class AnsweredClaimClass:
    """A class of claims a person answered a rule read wrongly: a defeat condition, as a rule.

    This predicate raises nothing itself. A person's "Wrong" on a class of claims is read by the
    pass at the run's watermark (``record/answers.py``). A class of claims is every claim that one
    rule made with one value. The pass records the answer, fired, on each claim of the class, and
    the record names this rule. So the retraction opens a rule that a person can read, as the
    copies answer does. A defeat condition is a rule (``02-defeaters.yaml``). This one is
    evaluated once per root and abstains.
    """

    name = ANSWERED_CLAIM_CLASS_PREDICATE
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise nothing: the answer is applied to the claims of the class it was given on."""
        del subject, parameters
        return None

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that it raises nothing, and where its answers are read."""
        del subject, parameters
        return Abstention(
            reason=(
                "it raises nothing: a person's answer that a rule read a class wrongly is "
                "recorded, fired, on each claim of the class"
            )
        )


@declare(
    ANSWERED_CLAIM_CLASS_PREDICATE,
    version="1",
    summary="Read a person's answer that a rule read a whole class of claims wrongly",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    reach=Reach.ROOT,
)
def _build_answered_claim_class() -> Predicate:
    return AnsweredClaimClass()
