"""A note written as a file name: an empty file whose name is a sentence.

People leave notes this way. An empty text file is named for what the writer wants the next
reader to know, such as that a culture was spoiled or which folder to ask about first. The
content of the note is its name, so the file is read as a note and not as an empty file.

An entry reads as a note (:func:`reads_as_a_note`) from the census alone, without opening it, when
all of these hold:

- it holds no bytes;
- it is a text file or has no extension (``note_extensions``, ``(none)`` for no extension), so an
  empty table or image is never a note, whatever it is called;
- its name, the extension set aside, is at least ``minimum_words`` pieces of letters alone, cut at
  spaces and punctuation, with spaces between at least ``minimum_words - 1`` of them
  (a sentence; ``kelt_vorn_zib_brisa`` is a name built from a template, not a note);
- no piece holds a digit: a number, a date or a sample's label is a data-like piece, and a name
  holding one is a data name.

The thresholds are unmeasured defaults. Four words is the shortest of the notes seen, and a digit
is the plainest mark of a data name.

A note is claimed as a note. It is never raised as an empty output, as an empty file, or as a file
that holds no data. ``content_unreadable`` and ``empty_among_data`` leave it out, through their
``notes_as_names`` switch, because its content is not missing. It is written where a person reads
it.

Each of the three rules reads what a note is through its own ``minimum_words`` and
``note_extensions`` (:func:`is_a_note`). The three hold three copies of these parameters, and a
person tunes them together. A rule's parameters are its own. If a rule deferred to the note
rule's claim instead, it would raise the note anyway, because a flag is raised all the same, and
it would record the note rule as its defeater. So the texts and parameters of the rules say to
tune the three at once.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ingence.core.schema import Entry, EntryType, RuleEffect
from ingence.rulebook.registry import Abstention, Concluded, Reads, declare

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from ingence.rulebook.registry import Predicate

__all__ = ["MINIMUM_WORDS", "NOTE_EXTENSIONS", "NoteInAFileName", "is_a_note", "reads_as_a_note"]

#: The fewest words a note's name holds (unmeasured, flagged).
MINIMUM_WORDS = 4
#: The extensions a note is written with; ``(none)`` stands for no extension.
NOTE_EXTENSIONS: tuple[str, ...] = ("txt", "text", "md", "(none)")

_PIECES = re.compile(r"[^\w]+|_+", re.UNICODE)
_DIGIT = re.compile(r"\d")
_LETTERS = re.compile(r"^[^\W\d_]+$", re.UNICODE)


def reads_as_a_note(
    entry: Entry,
    *,
    minimum_words: int = MINIMUM_WORDS,
    extensions: Collection[str] = NOTE_EXTENSIONS,
) -> bool:
    """Whether an entry is an empty file whose name is a sentence, from the census alone."""
    if entry.entry_type is not EntryType.FILE or entry.size_bytes != 0:
        return False
    extension = (entry.extension or "").casefold()
    if (extension or "(none)") not in {e.casefold() for e in extensions}:
        return False
    stem = entry.name
    if extension and stem.casefold().endswith("." + extension):
        stem = stem[: -len(extension) - 1]
    if _DIGIT.search(stem):
        return False
    pieces = [piece for piece in _PIECES.split(stem) if piece]
    if len(pieces) < minimum_words or not all(_LETTERS.match(piece) for piece in pieces):
        return False
    return stem.count(" ") >= minimum_words - 1


def is_a_note(entry: Entry, parameters: Mapping[str, object]) -> bool:
    """Whether an entry reads as a note by the thresholds a rule's parameters hold.

    ``minimum_words`` and ``note_extensions`` as the rule declares them, and this module's
    defaults where it leaves one out: one reading for the note rule and for every rule that
    leaves a note out, so tuning one threshold moves them together wherever the rules agree.
    """
    minimum = int(str(parameters.get("minimum_words", MINIMUM_WORDS)))
    listed = parameters.get("note_extensions", NOTE_EXTENSIONS)
    extensions = [str(e) for e in listed] if isinstance(listed, (list, tuple)) else []
    return reads_as_a_note(entry, minimum_words=minimum, extensions=extensions)


class NoteInAFileName:
    """An empty file whose name is a sentence: a note, its name its content."""

    name = "note_in_a_file_name"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return ``note`` for an empty text file whose name is a sentence, else None."""
        if not isinstance(subject, Entry) or not is_a_note(subject, parameters):
            return None
        return Concluded(values=("note",), inputs=(subject.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why the file is not a note: it holds bytes, or its name is not a sentence."""
        del parameters
        if isinstance(subject, Entry) and subject.size_bytes:
            return Abstention(reason="it holds bytes, so its content is there to read")
        return Abstention(reason="its name is not a sentence of words alone")


@declare(
    "note_in_a_file_name",
    version="1",
    summary="Classify an empty text file whose name is a sentence as a note, its name its content",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.ENTRY,
    parameters=("minimum_words", "note_extensions"),
    support_families=("filesystem_fact", "name_structure"),
)
def _build_note_in_a_file_name() -> Predicate:
    return NoteInAFileName()
