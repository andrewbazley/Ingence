"""Read a filled answers file as answers, and keep the file as it came.

The Questions page exports the questions as one file (``question_pack.py``). Its ``answers``
object is keyed by each question's target. Whoever fills it in returns that object, or the whole
file with the answers filled. Importing the file writes each answer as its author's, once the
person has said who wrote it (``record/imported.py``). The write goes through the answer route,
which is the Record's one way in. Nothing in this module writes to the Record.

An answer may hold what a row on the page takes: ``word``, ``reading``, ``correct_value``,
``described``, ``belongs_to``, and, for a meaning of the author's own, ``answer`` with
``theme_kind``. ``basis`` (or ``note``) is the author's reason, written beside the answer as its
note. An answer that holds none of these fields is blank and decides nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "ANSWER_FIELDS",
    "IMPORTS_DIR",
    "AnswerFileRefusedError",
    "AnswersRead",
    "keep_imported",
    "read_answers",
]

#: Where an imported file is kept, as it came, beside the Record and outside every run.
IMPORTS_DIR = "imports"

#: The fields an answer may carry that say something; ``basis`` and ``note`` say why.
ANSWER_FIELDS = ("word", "reading", "correct_value", "described", "belongs_to", "answer")


#: A field that files from older exports carry for whoever filled them in. The importing person
#: states who wrote a file, so this field is not an answer and the reader skips it.
_NAMED_THEIR_AUTHOR = frozenset({"suggested_by"})


class AnswerFileRefusedError(ValueError):
    """The file holds no answers the page can read, and nothing was written."""


@dataclass(frozen=True)
class AnswersRead:
    """Each answer by target, what was left blank, and what could not be read as an answer."""

    answers: dict[str, dict[str, str]] = field(default_factory=dict)
    blank: tuple[str, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()


def _spelled(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_answers(document: Any) -> AnswersRead:
    """Read a returned answers object, or the whole exported file with its answers filled."""
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError as error:
            message = f"the file is not JSON: {error}"
            raise AnswerFileRefusedError(message) from error
    if isinstance(document, dict) and isinstance(document.get("answers"), dict):
        document = document["answers"]
    if not isinstance(document, dict):
        message = "an answers file is one JSON object, each answer under what it is about"
        raise AnswerFileRefusedError(message)
    answers: dict[str, dict[str, str]] = {}
    blank: list[str] = []
    unreadable: list[tuple[str, str]] = []
    for target, said in document.items():
        key = str(target)
        if key in _NAMED_THEIR_AUTHOR:
            continue
        if isinstance(said, str):
            said = {"word": said}
        if not isinstance(said, dict):
            unreadable.append((key, "an answer is an object of words, or a word"))
            continue
        kept = {name: _spelled(value) for name, value in said.items() if _spelled(value)}
        if not any(kept.get(name) for name in ANSWER_FIELDS):
            blank.append(key)
            continue
        answers[key] = kept
    return AnswersRead(answers=answers, blank=tuple(blank), unreadable=tuple(unreadable))


def keep_imported(project: Path, document: Any, *, name: str, received_at: str) -> Path:
    """Keep an imported file under ``imports/`` as it came, with its name and when it came.

    Named by the moment and a digest of the content, created exclusively, never rewritten.
    Deleting the folder changes no run: each answer is in the Record.
    """
    directory = project / IMPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {"name": name, "received_at": received_at, "document": document},
        sort_keys=True,
        ensure_ascii=False,
        indent=1,
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    stamp = "".join(ch for ch in received_at if ch.isdigit())[:14] or "0"
    path = directory / f"{stamp}-{digest}.json"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return path
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path
