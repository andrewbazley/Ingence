"""An empty replicate among replicates that hold data is not a data point.

A replicate can be empty and still produce empty analysis files, because batch pipelines run on
every replicate whether it holds data or not. An empty replicate holds no data, so it is not a data
point. It should not weigh down an analysis where other replicates of the same condition hold
acceptable data.

So an empty file whose siblings hold data is concluded ``no_data_point``. A sibling is one of two
kinds. The first is a file with the same name except for its numbers, in the empty file's folder or
a folder beside it. The second is a file of the same batch template in the same folder, with the
same extension and the same first and last name parts. The conclusion is information that a reader
of an analysis uses to leave the file out of the weighing. It is never a question and never "empty
and old". The flag ``content_unreadable`` still raises the file as an empty output, which says why
its content cannot be read. This rule says what that means for the data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.schema import RuleEffect
from ingence.infer.subjects import EntryAmongSiblings
from ingence.rulebook.predicates.flag import empty_among_data
from ingence.rulebook.predicates.notes import is_a_note
from ingence.rulebook.registry import Abstention, Concluded, Reads, declare

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.rulebook.registry import Predicate

__all__ = ["NoDataPoint"]


class NoDataPoint:
    """An empty file among siblings holding data: an empty replicate or output, not a data point."""

    name = "empty_among_data"
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return ``no_data_point`` for an empty file among siblings holding data, else None.

        A note written as a file name is not a replicate. With ``notes_as_names`` the note's content
        is its name, and the rule leaves it out. The thresholds are the parameters this rule
        declares, ``minimum_words`` and ``note_extensions``.
        """
        if not isinstance(subject, EntryAmongSiblings):
            return None
        if parameters.get("notes_as_names") is True and is_a_note(subject.entry, parameters):
            return None
        siblings = subject.beside | subject.nearby | {f"template:{t}" for t in subject.templates}
        if not empty_among_data(subject.entry, frozenset(siblings)):
            return None
        return Concluded(values=("no_data_point",), inputs=(subject.entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the file holds bytes or nothing beside it does."""
        del parameters
        entry = getattr(subject, "entry", None)
        if entry is not None and entry.size_bytes:
            return Abstention(reason="the file holds data")
        return Abstention(reason="no file beside it of its name or its batch holds data")


@declare(
    "empty_among_data",
    version="3",
    summary="Classify an empty file among siblings holding data as no data point",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.ENTRY_AMONG_SIBLINGS,
    parameters=("minimum_words", "note_extensions", "notes_as_names"),
    support_families=("filesystem_fact", "name_structure"),
)
def _build_empty_among_data() -> Predicate:
    return NoDataPoint()
