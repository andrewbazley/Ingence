"""Import a file of answers under the author that a person names.

The Dashboard's Import asks what the file is and who wrote it (``record/imported.py``). It then
sends the file and that statement to the answer route, the Record's one way in, which calls
:func:`import_file`. Three kinds of file write answers:

- ``answers``: a filled answers file (``review/answer_file.py``), kept under ``imports/`` as it
  came, with each answer written on its own target.
- ``groupings``: a groupings sheet, read as ``ingence record import --check`` reads it
  (``workspace._sheet_preview``), with each answer it holds written on its group.
- ``record``: a file of Record events, the words a person taught in another project
  (``ingence teach export-answers --as-record``). Each event is written as an imported event that
  says which project it came from (``record/imported.py``).

Each answer is its author's decision, written as the page writes one (``decide.decide_many``).
A target that already has an answer keeps it and is listed as kept. Each other answer is refused
on its own terms, or written. Each event's source names the file. It also names who imported the
file where that is not the author, and for an agent's file it records that the person reviewed
it. The route checks the statement first. A refused statement writes nothing, not even the kept
copy.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from ingence.api.routes import decide
from ingence.record.imported import ImportRefusedError, ImportStatement, stated
from ingence.review.answer_file import (
    ANSWER_FIELDS,
    AnswerFileRefusedError,
    keep_imported,
    read_answers,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.storage.catalog import Catalog

__all__ = ["IMPORT_KINDS", "import_file"]

#: The kinds of file whose content is answers, each written under the author a person names.
IMPORT_KINDS = ("answers", "groupings", "record")

#: What an answer in a file may say: what a row on the page takes (``review/answer_file.py``).
_ANSWER_KEYS = (*ANSWER_FIELDS, "theme_kind", "where")


def _plain_name(value: object, fallback: str) -> str:
    named = str(value or "").strip() or fallback
    return PureWindowsPath(PurePosixPath(named).name).name or fallback


def _answer_decisions(
    project: Path, said: Mapping[str, Any], *, name: str, received_at: str
) -> tuple[list[decide.Decision], dict[str, Any], list[dict[str, str]], int]:
    document = said.get("document")
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError as error:
            message = f"{name} is not JSON, so it holds no answers: {error}"
            raise AnswerFileRefusedError(message) from error
    read = read_answers(document)
    kept = keep_imported(project, document, name=name, received_at=received_at)
    # Only what a row takes from a person is read from the file; anything else it says is kept
    # in the kept copy and decides nothing.
    decisions = [
        decide.Decision.from_body(
            {
                "target": target,
                **{key: answer[key] for key in _ANSWER_KEYS if key in answer},
                "note": answer.get("note") or answer.get("basis"),
            }
        )
        for target, answer in read.answers.items()
    ]
    refused = [{"target": target, "error": why} for target, why in read.unreadable]
    return decisions, {"kept_as": kept.name}, refused, len(read.blank)


def _grouping_decisions(
    project: Path, said: Mapping[str, Any], *, name: str, author: str
) -> tuple[list[decide.Decision], dict[str, Any], list[dict[str, str]], int]:
    from ingence.api.routes.workspace import _sheet_preview

    shown = _sheet_preview(
        project, {"document": said.get("document"), "name": name, "actor": author}
    )
    decisions = [
        decide.Decision(
            target=str(planned["target"]),
            word=planned.get("word") or None,
            note=planned.get("note") or None,
            correct_value=planned.get("correction") or None,
            sheet=shown["sheet"],
        )
        for planned in shown["planned"]
    ]
    refused = [
        {"target": "", "error": f"{count} refused: {why}"}
        for why, count in (shown.get("refused") or {}).items()
    ]
    return decisions, {}, refused, int(shown.get("blank") or 0)


def _import_record(
    project: Path,
    statement: ImportStatement,
    said: Mapping[str, Any],
    *,
    name: str,
    occurred_at: str,
) -> dict[str, Any]:
    """A file of Record events written as imported events, answer by answer."""
    from ingence.record.imported import apply_carried, read_record_file

    try:
        words = read_record_file(said.get("document"))
        report = apply_carried(
            project, words, statement=statement, imported_from_name=name, occurred_at=occurred_at
        )
    except ImportRefusedError as error:
        message = f"{error}; nothing was written"
        raise ValueError(message) from error
    return {
        "kind": "record",
        "file": name,
        "author": statement.author,
        "written": [
            {"sequence": event.sequence, "target": event.target, "kind": event.kind.value}
            for event in report.written
        ],
        "refused": report.refused,
        "kept": sorted(report.kept),
        "blank": 0,
        "left_out": sum(words.left_out.values()),
    }


def import_file(
    catalog: Catalog, project: Path, actor: object, said: Mapping[str, Any], *, occurred_at: str
) -> dict[str, Any]:
    """Write each answer a file holds under the author ``said`` names, and say what became of each.

    ``said`` is the page's ``import``: ``kind``, ``name``, ``document``, and the statement
    (``written_by``, ``agent_generated``, ``reviewed``). Raises ``ValueError`` for a statement or
    a file that cannot be imported, before anything is written.
    """
    try:
        statement = stated(
            actor,
            written_by=said.get("written_by"),
            agent_generated=bool(said.get("agent_generated")),
            reviewed=bool(said.get("reviewed")),
        )
    except ImportRefusedError as error:
        message = f"{error}; nothing was written"
        raise ValueError(message) from error
    kind = str(said.get("kind") or "")
    if kind not in IMPORT_KINDS:
        message = f"{kind!r} is not a file of answers; the kinds are {', '.join(IMPORT_KINDS)}"
        raise ValueError(message)
    name = _plain_name(said.get("name"), "answers.json" if kind == "answers" else "sheet.csv")
    if kind == "record":
        return _import_record(project, statement, said, name=name, occurred_at=occurred_at)
    if kind == "answers":
        decisions, kept, refused, blank = _answer_decisions(
            project, said, name=name, received_at=occurred_at
        )
    else:
        decisions, kept, refused, blank = _grouping_decisions(
            project, said, name=name, author=statement.author
        )
    source = {"imported_from": name, **kept, **statement.source()}
    context = decide.DecisionContext(catalog, project)
    # A target that is answered already keeps its answer, as an answer over a list does
    # (``decide.AlreadyAnsweredError``). An import never writes over a decision in force, and it
    # never writes the same one again when a file is imported twice.
    already = sorted(
        {decision.target for decision in decisions if context.in_force(decision.target)}
    )
    decisions = [decision for decision in decisions if decision.target not in already]
    written, not_written = decide.decide_many(
        context, decisions, actor=statement.author, occurred_at=occurred_at, also=source
    )
    return {
        "kind": kind,
        "file": name,
        **kept,
        "author": statement.author,
        "written": [
            {"sequence": event.sequence, "target": event.target, "kind": event.kind.value}
            for event in written
        ],
        "refused": refused + not_written,
        "kept": already,
        "blank": blank,
    }
