"""Read a review sheet into the Record: its verdicts as events, against the current run.

What each verdict word decides is ``record/verdicts.py``'s one table. This module reads a sheet. It
finds which column says what a row is about, which column carries the verdict, and what becomes of
a row that cannot be written.

A sheet is recognised by the identifier column it carries, never by its file's name. So a sheet
renamed in a spreadsheet program still imports, and a sheet of another shape is refused whole and
not read as something it is not:

| Column | Each row is about | Target |
|---|---|---|
| ``finding_id`` | the question the finding raised | ``question:<rule_id>:<raised>`` |
| ``candidate_id`` | one proposed pair | ``candidate:<candidate_id>`` |
| ``claim_id`` | one claim, from the rule a ``rule`` column names | ``claim:<claim_id>`` |
| ``class key`` | one class, under the one rule holding the key | ``class:<rule_id>:<digest>`` |
| ``term as written`` | the question raising the term | ``question:<rule_id>:<term>`` |

A class key names its rule with ``--rule`` where more than one grouping rule holds the key. A term
names its rule the same way. A claim's ``rule`` column, where there is one, must agree with the
claim.

Two teaching sheets import as well. What a person says about a kind of file or about a folder is a
decision too, and a spreadsheet is no place to keep one:

| Column | Each row is about | Target |
|---|---|---|
| ``extension`` | every file carrying one extension | ``extension:<ext>`` |
| ``finding_id``, no verdict | the folder its finding was raised about | ``folder:<entry_id>`` |

An extension's row becomes its ``worth reading`` word, which decides what ``verdicts.WORTH_READING``
says, with the person's description beside it. A folder's row becomes an annotation that carries the
person's description.

An ``extension`` column names what a row is about only on a sheet that carries none of the
identifier columns above. A sheet about findings or claims carries each file's extension as a
description of the file. A description is a column whose header ends ``(your words)``. On an
extension sheet, the column whose header begins ``format / program / instrument`` is a description
too. A finding sheet with neither a verdict nor a description is still refused whole.

An imported sheet says who wrote it on every event. The person importing states it
(``record/imported.py``). The event's ``source`` records who imported the sheet where that is not
the author. For an agent's sheet, it also records that the person reviewed it.

The verdict is the column whose header begins ``verdict``. A word must be one that the header offers
as well as one that the table holds. A stage claim's correction is the column whose header begins
``if wrong``, and a note is the column named ``note``. A term's answer is the column whose header
begins ``your answer``. It is a sense that the question offers, spelled as the sheet spells them,
``term [axis]``, or a definition in the same form on an axis of its own. A term answer goes through
:func:`~ingence.api.routes.decide.answer_question`, the Review view's own write. A sheet is refused
exactly what the page would be refused.

There is one decision per target. A row is nearly always one target. Where several rows reach one
question, for example several files raising one value, they are one decision about that question.
Ingence writes it once with every row it came from, and the rows must agree.

Every row comes to one of four ends, and each is counted:

- Left blank: the row has no verdict. It decides nothing and writes nothing.
- Written: one event per target through ``record/log.py``, against the current run.
- Already recorded: the decision in force on the target already says this, from this actor. A sheet
  imported again as it fills writes only what changed.
- Refused, counted by reason. A target that the current run does not hold is a stale sheet, and it
  cannot write (``decide.py``'s rule). A word that the sheet does not offer is a slip. Rows that
  disagree about one target are all refused, because a sheet that says two things about one question
  has not decided it.

A check resolves every row's target, blank rows included, and writes nothing. So a person can see
what a sheet would do before a word is filled in.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.api.routes.decide import (
    AnswerRefusedError,
    QuestionMovedError,
    current_questions,
)
from ingence.core.ids import PROJECT_SCOPE, Scope, ScopeKind
from ingence.core.schema import RecordEventKind
from ingence.record.log import append_event, latest_by_target, notes_by_target, read_events
from ingence.record.questions import question_target
from ingence.record.targets import FOLDER_PREFIX, NO_EXTENSION, extension_target
from ingence.record.verdicts import (
    CANDIDATE_WORDS,
    VERDICTS,
    WORTH_READING,
    Subject,
    VerdictRefusedError,
    candidate_subjects,
    claim_subjects,
    class_index,
    class_shown,
    class_takes,
    combined_subject,
    current_run,
    extension_counts,
    extension_event,
    finding_subjects,
    kind_for,
    verdict_payload,
    write_note,
    write_verdict,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.storage.catalog import Catalog

__all__ = [
    "IDENTIFIER_COLUMNS",
    "VALUE_COLUMNS",
    "SheetRefusedError",
    "SheetReport",
    "import_sheet",
]

#: The column naming what a row is about, and what kind of sheet carrying it makes.
IDENTIFIER_COLUMNS: dict[str, str] = {
    "finding_id": "finding",
    "candidate_id": "candidate",
    "claim_id": "claim",
    "class key": "class",
    "term as written": "term",
}

#: A column naming what a row is about only where none of the identifier columns is present:
#: a sheet about findings or claims carries each file's extension as a description of the file.
VALUE_COLUMNS: dict[str, str] = {"extension": "extension"}

#: A description column's header ends so: the words are the person's, recorded as written.
DESCRIPTION_SUFFIX = "(your words)"
#: On an extension sheet, the column naming what the extension belongs to.
BELONGS_PREFIX = "format / program / instrument"

_OFFERED = re.compile(r"\(([^)]*)\)")
_SENSE = re.compile(r"^(?P<term>.*?)\s*\[(?P<axis>[^\]]+)\]\s*$")

#: Why a row was refused, as the report counts it.
STALE = "the current run does not hold this target"
NOT_OFFERED = "the word is not one this sheet offers"
#: A word that the sheet offers but the row's own rule does not (a class's or a pair's words, as the
#: page offers them). This is where the page's preview and the sheet reader meet.
NOT_OFFERED_BY_RULE = "the word is not one this group's rule offers"
DISAGREES = "the sheet says two things about one target"
WRONG_RULE = "the row names a rule the claim is not from"
NOT_A_FOLDER = "the finding was not raised about a folder"
OTHER_FOLDER = "the row's folder is not the one its finding was raised about"


class SheetRefusedError(Exception):
    """The whole sheet was refused, and the message says why."""


@dataclass
class SheetReport:
    """What importing one sheet did, row by row, in counts."""

    sheet: str
    kind: str
    rows: int
    against_run_id: str
    check: bool
    #: Rows whose target the current run holds, blank rows included.
    in_run: int = 0
    blank: int = 0
    #: Rows a person left without a verdict and wrote a note on: kept as notes, never dropped.
    noted: int = 0
    written: int = 0
    already: int = 0
    refused: Counter[str] = field(default_factory=Counter)
    #: Answers that a term sheet held and did not write, because it contradicts itself on some term.
    #: A sheet is applied whole or not at all.
    withheld: int = 0
    #: The first full message for each reason, where it says more than the reason does.
    examples: dict[str, str] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    #: What a check would write, one entry per target: its target, the word (None for a note alone),
    #: the note, a correction, and the sheet's row numbers. A page can list each answer, and a
    #: person can take each one through the answer route. Only a check fills it.
    planned: list[dict[str, Any]] = field(default_factory=list)

    def refuse(self, reason: str, count: int = 1, *, example: str | None = None) -> None:
        self.refused[reason] += count
        if example and reason not in self.examples:
            self.examples[reason] = example


@dataclass(frozen=True, slots=True)
class _Row:
    number: int
    identifier: str
    verdict: str
    note: str
    correction: str
    rule: str
    folder: str


def _column(headers: Sequence[str], prefix: str) -> str | None:
    for header in headers:
        if header.strip().lower().startswith(prefix):
            return header
    return None


def _offered_on(kind: str, subject: Subject, word: str, predicates: dict[str, str]) -> bool:
    """Whether a row of this kind is offered the word, as the page offers it.

    A class takes its rule's words (``class_takes``) and a pair the pair's (``CANDIDATE_WORDS``),
    so a hand-edited sheet cannot write on a class what the answer route refuses there. Other
    kinds keep the header's words, which older sheets spell their own way (``stub``).
    """
    if kind == "candidate":
        return word in CANDIDATE_WORDS
    if kind != "class":
        return True
    return word in class_takes(predicates.get(subject.rule_id))


def _offered(header: str, table: Mapping[str, RecordEventKind] = VERDICTS) -> frozenset[str]:
    """The words a column's header offers, or every word of its table where it lists none."""
    found = _OFFERED.search(header)
    if not found:
        return frozenset(table)
    return frozenset(word.strip() for word in found.group(1).split("|") if word.strip())


def _read(sheet: Path) -> tuple[list[str], list[dict[str, str]]]:
    # utf-8-sig, because a spreadsheet program saving CSV puts a byte-order mark first.
    with sheet.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = [header.strip() for header in (reader.fieldnames or [])]
        reader.fieldnames = headers
        rows = [{key: (value or "") for key, value in row.items() if key} for row in reader]
    return headers, rows


def _kind(headers: Sequence[str]) -> str:
    present = [column for column in IDENTIFIER_COLUMNS if column in headers]
    if not present:
        for column, kind in VALUE_COLUMNS.items():
            if column in headers:
                return kind
        named = ", ".join([*IDENTIFIER_COLUMNS, *VALUE_COLUMNS])
        message = (
            f"the sheet carries none of the columns a row can be about ({named}), so nothing "
            "says what its rows decide"
        )
        raise SheetRefusedError(message)
    if len(present) > 1:
        message = f"the sheet carries {', '.join(present)}, and a row can be about one thing"
        raise SheetRefusedError(message)
    return IDENTIFIER_COLUMNS[present[0]]


def _decided(
    event: RecordEvent | None, kind: RecordEventKind, actor: str, said: dict[str, Any]
) -> bool:
    """Whether the decision in force on a target already says this, from this actor."""
    if event is None or event.kind is not kind or event.actor != actor:
        return False
    payload = json.loads(event.payload or "{}")
    return all(payload.get(name) == value for name, value in said.items())


def _described(headers: Sequence[str]) -> list[str]:
    """Return the columns holding a person's own words, in the sheet's order."""
    return [header for header in headers if header.strip().lower().endswith(DESCRIPTION_SUFFIX)]


@dataclass(frozen=True, slots=True)
class _Writing:
    """What every event an import writes carries beside its decision."""

    project: Path
    actor: str
    occurred_at: str
    against_run: str
    imported: Mapping[str, Any] | None

    def source(self, sheet: str, rows: Sequence[int]) -> dict[str, Any]:
        """Where a decision came from: the sheet, its rows, and who imported it."""
        found: dict[str, Any] = {"sheet": sheet, "rows": list(rows)}
        if self.imported:
            found.update(self.imported)
        return found


def import_sheet(
    catalog: Catalog,
    project: Path,
    sheet: Path,
    *,
    actor: str,
    occurred_at: str,
    rule_id: str | None = None,
    check: bool = False,
    imported: Mapping[str, Any] | None = None,
) -> SheetReport:
    """Import one sheet's verdicts into the Record against the current run, or check them.

    ``rule_id`` names the rule a class key or a term belongs to where the sheet does not say
    and more than one rule could. With ``check`` nothing is written and the report says what
    an import would do. ``imported`` is the person's statement of who wrote the sheet
    (``record/imported.py``), which every event records beside the sheet.
    """
    headers, rows = _read(sheet)
    kind = _kind(headers)
    try:
        against_run = current_run(catalog)
    except VerdictRefusedError as error:
        raise SheetRefusedError(str(error)) from error
    report = SheetReport(
        sheet=sheet.name, kind=kind, rows=len(rows), against_run_id=against_run, check=check
    )
    writing = _Writing(
        project=project,
        actor=actor,
        occurred_at=occurred_at,
        against_run=against_run,
        imported=imported,
    )
    if kind == "term":
        _import_terms(catalog, project, headers, rows, report, writing, rule_id)
        return report
    if kind == "extension":
        _import_extensions(catalog, headers, rows, report, writing)
        return report

    verdict_column = _column(headers, "verdict")
    if verdict_column is None:
        if kind == "finding" and _described(headers):
            _import_folder_descriptions(catalog, headers, rows, report, writing)
            return report
        message = (
            "the sheet carries no verdict column and no description in a person's own words, "
            "so nothing on it is a decision the import can take"
        )
        raise SheetRefusedError(message)
    offered = _offered(verdict_column)
    identifier_column = next(column for column in IDENTIFIER_COLUMNS if column in headers)
    correction_column = _column(headers, "if wrong")
    folder_column = "folder(s)" if "folder(s)" in headers else None
    parsed = [
        _Row(
            number=number,
            identifier=row.get(identifier_column, "").strip(),
            verdict=row.get(verdict_column, "").strip(),
            note=row.get("note", "").strip(),
            correction=row.get(correction_column, "").strip() if correction_column else "",
            rule=row.get("rule", "").strip(),
            folder=row.get(folder_column, "").strip() if folder_column else "",
        )
        for number, row in enumerate(rows, start=1)
    ]
    subjects = _subjects(catalog, kind, parsed, rule_id)

    groups: dict[str, list[tuple[_Row, Subject]]] = {}
    noted: dict[str, list[tuple[_Row, Subject]]] = {}
    predicates: dict[str, str] = (
        {
            str(rule): str(predicate or "")
            for rule, predicate in catalog.query("SELECT rule_id, predicate FROM rules").fetchall()
        }
        if kind == "class"
        else {}
    )
    for row in parsed:
        found = subjects.get(row.number)
        about: tuple[Subject, ...] = (
            found if isinstance(found, tuple) else (found,) if isinstance(found, Subject) else ()
        )
        if about:
            if kind == "claim" and row.rule and row.rule != about[0].rule_id:
                if row.verdict or row.note:
                    report.refuse(WRONG_RULE)
                else:
                    report.blank += 1
                continue
            report.in_run += 1
        if not row.verdict:
            if not row.note:
                report.blank += 1
                continue
            # A note without a verdict is kept. A person who wrote on a row and chose no word said
            # something about that subject, so the row becomes a note, which decides nothing and
            # displaces nothing.
            if not about:
                report.refuse(STALE if found is None else str(found))
                continue
            for subject in about:
                noted.setdefault(subject.target, []).append((row, subject))
            continue
        if not about:
            report.refuse(STALE if found is None else str(found))
            continue
        if row.verdict not in offered or row.verdict not in VERDICTS:
            report.refuse(NOT_OFFERED, example=f"row {row.number} says {row.verdict!r}")
            _keep_note(noted, row, about)
            continue
        if not _offered_on(kind, about[0], row.verdict, predicates):
            report.refuse(NOT_OFFERED_BY_RULE, example=f"row {row.number} says {row.verdict!r}")
            _keep_note(noted, row, about)
            continue
        for subject in about:
            groups.setdefault(subject.target, []).append((row, subject))

    events = read_events(project)
    in_force = latest_by_target(events)
    for target, members in groups.items():
        said = {(row.verdict, row.correction) for row, _ in members}
        if len(said) > 1:
            report.refuse(DISAGREES, len(members))
            # A note is never lost with its row's refused verdict. When rows disagree, their
            # verdicts are refused and each row's note is still kept.
            for row, subject in members:
                _keep_note(noted, row, (subject,))
            continue
        (word, correction), subject = next(iter(said)), members[0][1]
        notes = list(dict.fromkeys(row.note for row, _ in members if row.note))
        note = " | ".join(notes) or None
        subject = _combined(subject, [found for _, found in members])
        claim = {"verdict": word, "note": note, "correct_stage": correction or None}
        raised = subject.about.get("raised")
        proposed = subject.about.get("primitive")
        kind = kind_for(
            word,
            raised=str(raised) if raised is not None else None,
            primitive=str(proposed) if proposed is not None else None,
        )
        if _decided(in_force.get(target), kind, actor, claim):
            report.already += 1
            continue
        if check:
            # A check refuses what the import would. The verdict's payload is built as the write
            # builds it, so a correction that the payload cannot carry is refused here too, and the
            # rows' notes are kept, and nothing is planned as written.
            try:
                verdict_payload(subject, word, note=note, correct_stage=correction or None)
            except VerdictRefusedError as error:
                report.refuse(
                    "the verdict does not stand as written", len(members), example=str(error)
                )
                for row, member in members:
                    _keep_note(noted, row, (member,))
                continue
            report.written += 1
            report.planned.append(
                {
                    "target": target,
                    "word": word,
                    "note": note,
                    "correction": correction or None,
                    "rows": [row.number for row, _ in members],
                }
            )
            continue
        try:
            event = write_verdict(
                project,
                subject,
                word,
                actor=actor,
                occurred_at=occurred_at,
                against_run_id=against_run,
                note=note,
                correct_stage=correction or None,
                source=writing.source(sheet.name, [row.number for row, _ in members]),
            )
        except VerdictRefusedError as error:
            report.refuse("the verdict does not stand as written", len(members), example=str(error))
            for row, member in members:
                _keep_note(noted, row, (member,))
            continue
        report.written += 1
        report.events.append(event.event_id)
    _write_notes(noted, report, writing, notes_by_target(events))
    return report


def _keep_note(
    noted: dict[str, list[tuple[_Row, Subject]]], row: _Row, about: Sequence[Subject]
) -> None:
    """Keep a row's note as a note about each subject, where its verdict could not be written."""
    if row.note:
        for subject in about:
            noted.setdefault(subject.target, []).append((row, subject))


def _noted_already(events: Sequence[RecordEvent], actor: str, note: str) -> bool:
    """Whether this actor has already written this note about the target."""
    return any(
        event.actor == actor and json.loads(event.payload or "{}").get("note") == note
        for event in events
    )


def _write_notes(
    noted: Mapping[str, Sequence[tuple[_Row, Subject]]],
    report: SheetReport,
    writing: _Writing,
    in_record: Mapping[str, Sequence[RecordEvent]],
) -> None:
    """Write each target's notes as one note event, counted apart from the verdicts."""
    for target, members in noted.items():
        note = " | ".join(dict.fromkeys(row.note for row, _ in members))
        if _noted_already(in_record.get(target, ()), writing.actor, note):
            report.already += 1
            continue
        report.noted += 1
        if report.check:
            report.planned.append(
                {
                    "target": target,
                    "word": None,
                    "note": note,
                    "correction": None,
                    "rows": [row.number for row, _ in members],
                }
            )
            continue
        subject = _combined(members[0][1], [found for _, found in members])
        event = write_note(
            writing.project,
            subject,
            note,
            actor=writing.actor,
            occurred_at=writing.occurred_at,
            against_run_id=writing.against_run,
            source=writing.source(report.sheet, [row.number for row, _ in members]),
        )
        report.events.append(event.event_id)


def _combined(first: Subject, found: Sequence[Subject]) -> Subject:
    """One subject for rows reaching one target: a question names every finding it came from.

    :func:`~ingence.record.verdicts.combined_subject`, which the Dashboard's question page
    writes through as well, so a page and a sheet spell one decision alike.
    """
    return combined_subject(first, found)


def _subjects(
    catalog: Catalog, kind: str, rows: Sequence[_Row], rule_id: str | None
) -> dict[int, Subject | tuple[Subject, ...] | str | None]:
    """Return, per row number, what the row is about in the current run, or why nothing is.

    A row about a finding raising several values is about each of its questions (a tuple).
    """
    identifiers = [row.identifier for row in rows if row.identifier]
    found: Mapping[str, Subject | tuple[Subject, ...] | str]
    if kind == "finding":
        found = finding_subjects(catalog, identifiers, each_value=True)
    elif kind == "candidate":
        found = candidate_subjects(catalog, identifiers)
    elif kind == "claim":
        found = claim_subjects(catalog, identifiers)
    else:
        classes = class_index(catalog)
        found_classes = {
            row.number: classes.find(row.identifier, rule_id=rule_id, folder=row.folder or None)
            for row in rows
            if row.identifier
        }
        # The files each class holds now travel with the answer.
        return {
            number: class_shown(catalog, held) if isinstance(held, Subject) else held
            for number, held in found_classes.items()
        }
    return {row.number: found.get(row.identifier) for row in rows}


@dataclass(frozen=True, slots=True)
class _TermRow:
    """One answered row of a term sheet, read and resolved, before anything is decided."""

    number: int
    term: str
    question: Mapping[str, Any]
    kind: RecordEventKind
    answer: str | None
    axis: str | None
    note: str | None

    @property
    def decision(self) -> tuple[RecordEventKind, str | None, str | None]:
        """What the row decides; two rows about one target deciding alike are one decision."""
        return self.kind, self.answer, self.axis


#: Why answers a sheet otherwise held were not written: it contradicts itself somewhere.
WITHHELD = "the sheet says two things about a term, so none of it was written"


def _import_terms(
    catalog: Catalog,
    project: Path,
    headers: Sequence[str],
    rows: Sequence[dict[str, str]],
    report: SheetReport,
    writing: _Writing,
    rule_id: str | None,
) -> None:
    """Answer each term as the Review view would, through its own validation and its own write.

    The steps are read, group, validate, then write. Rows answered one at a time against a view of
    the Record read once would write two events for two rows about one term, or one event and a
    refusal, depending on their order. A check would also count a row as written before the Review
    view's validation had seen it. So:

    1. Every row is read and its term's question found.
    2. Rows are grouped by the decision's target. Rows that decide alike are one decision (their
       notes are joined, and their row numbers are its source). Rows that decide differently are a
       conflict, found before anything is written.
    3. Each decision is validated by :func:`~ingence.api.routes.decide.prepare_answer`, the Review
       view's own path, in a check exactly as in a write.
    4. A sheet that holds a conflict writes nothing at all, notes included, and says how many
       answers it withheld. Otherwise every prepared answer is written. Only this last step differs
       between a check and a write.
    """
    from ingence.api.routes.decide import prepare_answer, write_prepared

    actor, occurred_at = writing.actor, writing.occurred_at
    answer_column = _column(headers, "your answer")
    if answer_column is None:
        message = "a term sheet carries a column beginning 'your answer', and this one does not"
        raise SheetRefusedError(message)
    questions = current_questions(catalog)
    raised_by: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        raised_by.setdefault(str(question["raised"]), []).append(question)
    events = read_events(project)
    in_force = latest_by_target(events)
    noted: dict[str, list[tuple[_Row, Subject]]] = {}

    # 1. Read every row.
    answered: dict[str, list[_TermRow]] = {}
    for number, row in enumerate(rows, start=1):
        term = row.get("term as written", "").strip()
        said = row.get(answer_column, "").strip()
        note = row.get("note", "").strip() or None
        asked = [
            question
            for question in raised_by.get(term, [])
            if rule_id is None or question["rule_id"] == rule_id
        ]
        if len(asked) == 1:
            report.in_run += 1
        if not said:
            if note and len(asked) == 1:
                question = asked[0]
                subject = Subject(
                    target=question_target(str(question["rule_id"]), term),
                    rule_id=str(question["rule_id"]),
                    rule_version=str(question.get("rule_version") or ""),
                    about={"raised": term},
                )
                noted.setdefault(subject.target, []).append(
                    (_Row(number, term, "", note, "", "", ""), subject)
                )
            elif note:
                report.refuse(
                    STALE if not asked else "more than one rule raised this term; name the rule"
                )
            else:
                report.blank += 1
            continue
        if not asked:
            report.refuse(STALE)
            continue
        if len(asked) > 1:
            rules = ", ".join(sorted(str(question["rule_id"]) for question in asked))
            report.refuse("more than one rule raised this term; name the rule", example=rules)
            continue
        question = asked[0]
        kind, answer, axis = _term_answer(question, said)
        target = question_target(str(question["rule_id"]), term)
        answered.setdefault(target, []).append(
            _TermRow(number, term, question, kind, answer, axis, note)
        )

    # 2. Group by the decision's target; a conflict is found before anything is written.
    conflicted = False
    decisions: list[tuple[str, list[_TermRow]]] = []
    for target, members in answered.items():
        if len({member.decision for member in members}) > 1:
            conflicted = True
            report.refuse(
                DISAGREES,
                len(members),
                example="rows " + ", ".join(str(member.number) for member in members),
            )
            continue
        decisions.append((target, members))

    # 3. Validate each decision through the Review view's own path, check or write alike.
    prepared = []
    for target, members in decisions:
        first = members[0]
        notes = list(dict.fromkeys(member.note for member in members if member.note))
        note = " | ".join(notes) or None
        wanted: dict[str, Any] = {"note": note}
        if first.answer:
            wanted["answer"] = first.answer
        if first.axis:
            wanted["theme_kind"] = first.axis
        if _decided(in_force.get(target), first.kind, actor, wanted):
            report.already += 1
            continue
        try:
            prepared.append(
                prepare_answer(
                    catalog,
                    rule_id=str(first.question["rule_id"]),
                    raised=first.term,
                    kind=first.kind,
                    answer=first.answer,
                    theme_kind=first.axis,
                    note=note,
                    questions=questions,
                    source=writing.source(report.sheet, [member.number for member in members]),
                )
            )
        except QuestionMovedError as error:
            report.refuse(STALE, len(members), example=str(error))
        except AnswerRefusedError as error:
            report.refuse(
                "the Questions page refuses this answer", len(members), example=str(error)
            )

    # 4. All or nothing, and only the writing differs between a check and a write.
    if conflicted:
        report.withheld = len(prepared)
        if report.withheld:
            report.examples.setdefault(WITHHELD, f"{report.withheld} answers withheld")
        return
    for ready in prepared:
        report.written += 1
        if report.check:
            continue
        event = write_prepared(project, ready, actor=actor, occurred_at=occurred_at)
        report.events.append(event.event_id)
    _write_notes(noted, report, writing, notes_by_target(events))


def _term_answer(
    question: Mapping[str, Any], said: str
) -> tuple[RecordEventKind, str | None, str | None]:
    """Read a term answer: a sense the question offers, a definition, or ``cannot_say``.

    Spelled as the sheet spells a sense, ``term [axis]``. A term the question offers is an
    assignment, and its axis is the engine's; any other term is a definition, and states its
    axis, because a sense on no axis is one no rule reads. What is left over is passed on as
    written, and the Review view's own write says what is wrong with it.
    """
    if said == "cannot_say":
        return RecordEventKind.ANNOTATE, None, None
    spelled = _SENSE.match(said)
    term, axis = (
        (spelled.group("term").strip(), spelled.group("axis").strip()) if spelled else (said, None)
    )
    offered = {str(sense["term"]) for sense in question.get("senses", [])}
    if term in offered:
        return RecordEventKind.ASSIGN, term, axis
    return RecordEventKind.DEFINE, term, axis


def _extension_counts(catalog: Catalog) -> dict[str, tuple[int, int]]:
    """Return, per extension as the census stores it, the files carrying it and their bytes."""
    return extension_counts(catalog)


def _spelled_extension(value: str) -> str:
    """An extension as the census stores it; ``(none)`` or nothing for the files without one."""
    if value.strip().casefold() == NO_EXTENSION:
        return ""
    from ingence.core.extensions import canonical_extension

    return canonical_extension(value)


def _import_extensions(
    catalog: Catalog,
    headers: Sequence[str],
    rows: Sequence[dict[str, str]],
    report: SheetReport,
    writing: _Writing,
) -> None:
    """Take each extension's worth-reading word, and the person's words about it.

    One event per extension, about every file carrying it (``extension:<ext>``): the word
    decides what ``verdicts.WORTH_READING`` says, and a row carrying a description and no word
    is an annotation. An extension no file in the current run carries is refused as stale,
    on ``decide.py``'s rule, because a decision about a kind of file nobody has is a decision
    about nothing anybody was looking at.
    """
    worth_column = _column(headers, "worth reading")
    described = _described(headers)
    belongs_column = _column(headers, BELONGS_PREFIX)
    if worth_column is None and not described:
        message = (
            "an extension sheet carries a column beginning 'worth reading' or a description in "
            "a person's own words, and this one carries neither"
        )
        raise SheetRefusedError(message)
    offered = _offered(worth_column, WORTH_READING) if worth_column else frozenset()
    counts = _extension_counts(catalog)
    in_force = latest_by_target(read_events(writing.project))

    groups: dict[str, list[tuple[int, dict[str, str | None]]]] = {}
    for number, row in enumerate(rows, start=1):
        extension = _spelled_extension(row.get("extension", ""))
        held = extension in counts
        if held:
            report.in_run += 1
        word = row.get(worth_column, "").strip().lower() if worth_column else ""
        texts = [row.get(column, "").strip() for column in described]
        said: dict[str, str | None] = {
            "worth_reading": word or None,
            "described": " | ".join(text for text in texts if text) or None,
            "belongs_to": (row.get(belongs_column, "").strip() or None) if belongs_column else None,
            "note": row.get("note", "").strip() or None,
        }
        if not any(said.values()):
            report.blank += 1
            continue
        if not held:
            report.refuse(STALE)
            continue
        if word and (word not in offered or word not in WORTH_READING):
            report.refuse(NOT_OFFERED, example=f"row {number} says {word!r}")
            continue
        groups.setdefault(extension, []).append((number, said))

    for extension, members in groups.items():
        if len({tuple(sorted(said.items())) for _, said in members}) > 1:
            report.refuse(DISAGREES, len(members))
            continue
        said = members[0][1]
        # A person's words about the extension teach (an annotation). A bare note decides nothing
        # and is kept as one. What the run held when the person answered travels with the answer: a
        # word about thousands of files is different evidence from the same word about two. The
        # spelling is the same as on the Dashboard's extension page (``extension_event``).
        files, size = counts[extension]
        kind, payload = extension_event(
            extension,
            said,
            files=files,
            size=size,
            source=writing.source(report.sheet, [number for number, _ in members]),
        )
        target = extension_target(extension)
        if _decided(in_force.get(target), kind, writing.actor, dict(said)):
            report.already += 1
            continue
        if report.check:
            report.written += 1
            continue
        event = append_event(
            writing.project,
            kind=kind,
            actor=writing.actor,
            occurred_at=writing.occurred_at,
            target=target,
            against_run_id=writing.against_run,
            payload=payload,
            scope=PROJECT_SCOPE,
        )
        report.written += 1
        report.events.append(event.event_id)


def _folder_findings(
    catalog: Catalog, finding_ids: Sequence[str]
) -> dict[str, tuple[str, str, str, list[str]]]:
    """Return each finding's rule, version, subject and raised values, from the current run."""
    return {
        str(finding_id): (str(rule_id), str(rule_version), str(subject), list(raised or []))
        for finding_id, rule_id, rule_version, subject, raised in catalog.query(
            "SELECT finding_id, rule_id, rule_version, subject, raised FROM findings "
            "WHERE finding_id IN (SELECT unnest(?::VARCHAR[]))",
            [sorted(set(finding_ids))],
        ).fetchall()
    }


def _import_folder_descriptions(
    catalog: Catalog,
    headers: Sequence[str],
    rows: Sequence[dict[str, str]],
    report: SheetReport,
    writing: _Writing,
) -> None:
    """Take a person's words about each folder a finding was raised about, as an annotation.

    The target is the folder, not the finding's question: what a folder holds is a fact
    about the folder whichever rule raised it, so two rules raising one folder are one decision
    and the rows must agree. The folder is the finding's own subject; a ``folder`` column, where
    the sheet has one, must name the same folder. The event carries the folder's path as its
    scope, which is how a folder-scoped answer spells one, and names the findings the rows came
    from without claiming to be a verdict on their rule, so no precision figure counts it.
    """
    described = _described(headers)
    folder_column = "folder" if "folder" in headers else None
    identifiers = [row.get("finding_id", "").strip() for row in rows]
    found = _folder_findings(catalog, [identifier for identifier in identifiers if identifier])
    folders = sorted(
        {
            subject.split(":", 1)[1]
            for _, _, subject, _ in found.values()
            if subject.startswith(f"{FOLDER_PREFIX}:")
        }
    )
    paths = {
        str(entry_id): str(path)
        for entry_id, path in catalog.query(
            "SELECT entry_id, relative_path FROM entries "
            "WHERE entry_id IN (SELECT unnest(?::VARCHAR[]))",
            [folders],
        ).fetchall()
    }
    in_force = latest_by_target(read_events(writing.project))

    groups: dict[str, list[tuple[int, dict[str, str | None], str]]] = {}
    for number, (row, identifier) in enumerate(zip(rows, identifiers, strict=True), start=1):
        finding = found.get(identifier)
        if finding is not None:
            report.in_run += 1
        texts = [row.get(column, "").strip() for column in described]
        said: dict[str, str | None] = {
            "described": " | ".join(text for text in texts if text) or None,
            "note": row.get("note", "").strip() or None,
        }
        if not any(said.values()):
            report.blank += 1
            continue
        if finding is None:
            report.refuse(STALE)
            continue
        subject = finding[2]
        if not subject.startswith(f"{FOLDER_PREFIX}:"):
            report.refuse(NOT_A_FOLDER, example=f"row {number} is about {subject}")
            continue
        named = row.get(folder_column, "").strip() if folder_column else ""
        if named and named != subject:
            report.refuse(OTHER_FOLDER, example=f"row {number} names {named}")
            continue
        groups.setdefault(subject, []).append((number, said, identifier))

    for subject, members in groups.items():
        if len({tuple(sorted(said.items())) for _, said, _ in members}) > 1:
            report.refuse(DISAGREES, len(members))
            continue
        said = members[0][1]
        folder_kind = RecordEventKind.ANNOTATE if said["described"] else RecordEventKind.NOTE
        if folder_kind is RecordEventKind.NOTE and _noted_already(
            notes_by_target(read_events(writing.project)).get(subject, ()),
            writing.actor,
            str(said["note"]),
        ):
            report.already += 1
            continue
        if _decided(in_force.get(subject), folder_kind, writing.actor, dict(said)):
            report.already += 1
            continue
        if report.check:
            report.written += 1
            continue
        path = paths.get(subject.split(":", 1)[1])
        findings = [
            {
                "finding_id": identifier,
                "rule_id": found[identifier][0],
                "rule_version": found[identifier][1],
                "raised": found[identifier][3],
            }
            for identifier in sorted({identifier for _, _, identifier in members})
        ]
        event = append_event(
            writing.project,
            kind=folder_kind,
            actor=writing.actor,
            occurred_at=writing.occurred_at,
            target=subject,
            against_run_id=writing.against_run,
            payload={
                **{name: value for name, value in said.items() if value},
                "path": path,
                "findings": findings,
                "source": writing.source(report.sheet, [number for number, _, _ in members]),
            },
            scope=Scope(ScopeKind.FOLDER, path) if path else PROJECT_SCOPE,
        )
        report.written += 1
        report.events.append(event.event_id)
