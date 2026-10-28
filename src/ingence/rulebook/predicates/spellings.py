"""Each spelling a run read as a Dictionary spelling it is a slip of, raised with its count.

The correction is a stated assumption that a person can see and reverse.

The resolver binds a word of letters only to the one Dictionary spelling it is near enough to
(:meth:`~ingence.dictionary.store.DictionaryStore.correct`), marking the binding ``corrected``
and ``name:spelling``. This rule reads a root's bindings and raises one finding per spelling and
term: "hoescht is read as hoechst", with how many files write it. Reversing it is one answer on
the word (a meaning, or "unrelated" to the spelling: ``distinct:<a>+<b>``), which the next
reading of the names honours.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from ingence.core.schema import DictionaryBinding, MatchQuality, RuleEffect
from ingence.dictionary.discover import spelling_read_as
from ingence.rulebook.registry import Abstention, Raised, Reads, declare

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.rulebook.registry import Predicate

__all__ = ["SpellingReadAs", "read_as"]


def read_as(spelling: str, matched: str) -> str:
    """The words a corrected spelling is raised in: "{spelling} is read as {matched}", the
    Dictionary spelling it was read as (its term where an older binding carries no spelling)."""
    return f"{spelling} is read as {matched}"


class SpellingReadAs:
    """Each spelling read as a Dictionary spelling it is a slip of, with how many files write it."""

    name = "spelling_read_as"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise one value per spelling and term, resting on the bindings that read it."""
        floor = int(str(parameters.get("minimum_files", 1)))
        files: dict[tuple[str, str], set[str]] = defaultdict(set)
        inputs: dict[tuple[str, str], set[str]] = defaultdict(set)
        for held in getattr(subject, "entries", ()):
            spelled = {
                component.component_id: str(component.raw_value or component.normalized_value)
                for component in held.components
            }
            for binding in held.bindings:
                if (
                    isinstance(binding, DictionaryBinding)
                    and binding.quality is MatchQuality.CORRECTED
                    and binding.term
                ):
                    key = (
                        spelled.get(binding.component_id, binding.component_id),
                        spelling_read_as(binding.constraint) or binding.term,
                    )
                    files[key].add(held.entry.entry_id)
                    inputs[key].add(binding.binding_id)
        raised = sorted(key for key, held in files.items() if len(held) >= floor)
        if not raised:
            return None
        return Raised(
            values=tuple(read_as(spelling, term) for spelling, term in raised),
            inputs=tuple(sorted(set().union(*(inputs[key] for key in raised)))),
            reason="; ".join(
                f"{read_as(spelling, term)} in {len(files[(spelling, term)])} "
                f"file{'s' if len(files[(spelling, term)]) != 1 else ''}"
                for spelling, term in raised
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no name here was read as a slip of a Dictionary spelling."""
        return Abstention(reason="no word here is read as a slip of a Dictionary spelling")


@declare(
    "spelling_read_as",
    version="1",
    summary="Raise each spelling read as a Dictionary spelling it is a slip of, with its count",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=("minimum_files",),
)
def _build_spelling_read_as() -> Predicate:
    return SpellingReadAs()
