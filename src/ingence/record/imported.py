"""Who wrote a file that a person imports: their own statement, and the author each answer takes.

Importing asks what the file is and who wrote it. The choices are one of the people who have
answered in this project, or "Agent-generated". A file that an agent wrote is imported only once the
person confirms they reviewed its answers. Each answer is then written as ``<their name>_agent``.
Any other choice writes the named person as the author. Where the author is not the person
importing, the event says who imported it.

The statement is the person's, and it is the only place an agent is named. Nothing here guesses an
author, reads a name from inside the file, or treats any stored name specially. A Record whose
events carry any author loads and shows them as written.

The statement travels with every event that an import writes, in the event's ``source``.
``imported_by`` says who imported the file, where that is not the author. For an agent's file,
``agent_generated`` comes with ``reviewed_by``, the person who confirmed the review.

The words a person taught travel too. A project's in-force answers about words
(:data:`CARRIED_PREFIXES`) are carried into another project as imported events.
:func:`apply_carried` writes each one again in the new project's Record. Its author is the person
who brings it in, as Import says who wrote a file. Its ``source`` says "imported from <project>",
and its payload keeps the original author and moment. The answers win as a person's answers do,
because they are a person's answers. The one withdrawal that every event has withdraws each of them.
Answers kept below one folder name that project's folders, so Ingence counts them and leaves them
out. The same answers travel between machines as a file of Record events (:func:`record_file`,
``ingence teach export-answers --as-record``), which Import reads back (:func:`read_record_file`). A
Record event is made against a run that the project has published (``record/log.py``), so Ingence
writes a carried answer once the project has been read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ingence.dictionary.store import DictionaryStore

__all__ = [
    "AGENT_SUFFIX",
    "CARRIED_PREFIXES",
    "RECORD_FORMAT",
    "CarriedAnswer",
    "CarriedWords",
    "CarryReport",
    "ImportRefusedError",
    "ImportStatement",
    "agent_author",
    "apply_carried",
    "carried_words",
    "imported_from",
    "read_record_file",
    "record_authors",
    "record_file",
    "stated",
]

#: The targets whose answers are a person's words, which travel to another project: what a word
#: means (``term:``), how a spelling a capital divides reads (``split:``), two spellings that are
#: unrelated (``distinct:``), and a sense or a spelling of one turned off or back on (``sense:``,
#: ``surface:``): exactly the answers a person may withdraw one at a time
#: (``record.log.WITHDRAWABLE_PREFIXES``), so each carried answer can be taken back on its own.
CARRIED_PREFIXES: tuple[str, ...] = ("term", "split", "distinct", "sense", "surface")

#: What a file of Record events says it is, and the version of its shape.
RECORD_FORMAT = "ingence-record-events"
RECORD_FORMAT_VERSION = 1

#: What an agent's answers are written under, after the name of the person who reviewed them.
AGENT_SUFFIX = "_agent"

#: A name is written with every event it authors; the decider's own ceiling (``api/decider``).
MAX_NAME_LENGTH = 120


class ImportRefusedError(ValueError):
    """The import was not made, nothing was written, and the message says why."""


@dataclass(frozen=True, slots=True)
class ImportStatement:
    """Who is importing, who wrote the file, and what every event it writes says of both."""

    importer: str
    author: str
    agent_generated: bool = False

    def source(self) -> dict[str, Any]:
        """What each event's ``source`` carries beside where the answer came from."""
        said: dict[str, Any] = {}
        if self.author != self.importer:
            said["imported_by"] = self.importer
        if self.agent_generated:
            said["agent_generated"] = True
            said["reviewed_by"] = self.importer
        return said


def _name(value: object, *, what: str) -> str:
    spelled = " ".join(str(value or "").split())
    if len(spelled) > MAX_NAME_LENGTH:
        message = f"{what} is at most {MAX_NAME_LENGTH} characters"
        raise ImportRefusedError(message)
    if any(not character.isprintable() for character in spelled):
        message = f"{what} holds a character that does not print"
        raise ImportRefusedError(message)
    return spelled


def agent_author(person: str) -> str:
    """The author an agent's answers take once ``person`` confirms reviewing them."""
    return f"{person}{AGENT_SUFFIX}"


def stated(
    importer: object,
    *,
    written_by: object = None,
    agent_generated: bool = False,
    reviewed: bool = False,
) -> ImportStatement:
    """Read a person's statement of who wrote a file, refusing one that cannot be recorded.

    ``written_by`` empty means the person importing wrote it. ``agent_generated`` needs
    ``reviewed``: without it nothing is written. A name ending ``_agent`` is refused as a
    person's, since only the agent statement, with its confirmation, writes one.
    """
    person = _name(importer, what="the name of whoever imports")
    if not person:
        message = "say who is importing: every answer is recorded with who made it"
        raise ImportRefusedError(message)
    named = _name(written_by, what="the author's name")
    if agent_generated:
        if named:
            message = "a file is written by a person or by an agent, not both"
            raise ImportRefusedError(message)
        if not reviewed:
            message = "an agent's answers are imported once you confirm you reviewed them"
            raise ImportRefusedError(message)
        return ImportStatement(importer=person, author=agent_author(person), agent_generated=True)
    # One person spelled two ways is the person importing.
    author = person if not named or named.casefold() == person.casefold() else named
    if author.casefold().endswith(AGENT_SUFFIX):
        message = f"{author} names an agent: import the file as agent-generated"
        raise ImportRefusedError(message)
    return ImportStatement(importer=person, author=author)


def record_authors(project: Path, *, decider: str | None = None) -> list[str]:
    """The people who have answered in this project, the machine's decider first.

    Every author in the Record, withdrawn decisions included, except those an agent statement
    wrote (``<name>_agent``), which only that statement writes again.
    """
    from ingence.record.log import read_events

    seen: dict[str, str] = {}
    for event in read_events(project, withdrawn=True, moved=False):
        name = " ".join(str(event.actor or "").split())
        if name and not name.casefold().endswith(AGENT_SUFFIX):
            seen.setdefault(name.casefold(), name)
    first = " ".join(str(decider or "").split())
    others = sorted(
        (name for key, name in seen.items() if key != first.casefold()), key=str.casefold
    )
    return [first, *others] if first else others


def imported_from(source: Mapping[str, Any] | None) -> str | None:
    """Where an imported answer came from, in neutral words: the file's name, or "a file".

    Read from what an import wrote in the event's ``source``, including events stored before
    the import statement existed: a sheet's name, a kept file's name, or only who filled it in,
    whose name is not shown. None for an answer given on a page, including one given beside
    somebody's guess and saying otherwise (``guess_taken: false``).
    """
    if not isinstance(source, dict):
        return None
    for key in ("imported_from", "sheet", "advice_file"):
        named = source.get(key)
        if isinstance(named, str) and named.strip():
            return named.strip()
    # A stored answer given beside somebody's guess and differing from it is the person's own.
    taken = source.get("guess_taken") is not False
    return "a file" if source.get("prefilled_by") and taken else None


# ---------------------------------------------------------------------------------------------
# The words a person taught, carried into another project.
# ---------------------------------------------------------------------------------------------

#: What an answer left out of a carry is counted under.
SCOPED_TO_A_FOLDER = "kept below one folder"


@dataclass(frozen=True, slots=True)
class CarriedAnswer:
    """One in-force answer about words, as it stood in the project it was given in."""

    target: str
    #: The event kind, as the Record spells it (``define``, ``assign``, ``reject`` ...).
    kind: str
    payload: dict[str, Any]
    #: Who gave it and when, in the project it came from.
    actor: str
    occurred_at: str
    #: The name of the project it came from, where the file carries answers of several.
    source: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "kind": self.kind,
            "actor": self.actor,
            "occurred_at": self.occurred_at,
            "payload": self.payload,
            **({"from": self.source} if self.source else {}),
        }


@dataclass(frozen=True, slots=True)
class CarriedWords:
    """The answers that travel, in canonical order, and the counts of those that were left out."""

    answers: tuple[CarriedAnswer, ...] = ()
    left_out: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.answers)


def _carries(target: str, kind: str) -> bool:
    """Whether an answer on ``target`` of ``kind`` is a person's words, so that it travels."""
    from ingence.core.schema import RecordEventKind

    prefix, _, rest = target.partition(":")
    if prefix not in CARRIED_PREFIXES or not rest:
        return False
    return kind not in (RecordEventKind.NOTE.value, RecordEventKind.WITHDRAW.value)


def carried_words(project: Path, *, source: str = "") -> CarriedWords:
    """The in-force answers about words in ``project``'s Record, ready to be carried.

    The result holds the latest decision on each target that :data:`CARRIED_PREFIXES` names,
    canonically ordered by target. A withdrawn decision is not in force, and a note decides nothing.
    An answer kept below one folder is counted and left out, because a folder in one project is not
    a folder in another.
    """
    from ingence.core.ids import ScopeKind
    from ingence.record.log import latest_by_target, read_events

    kept: list[CarriedAnswer] = []
    left_out: dict[str, int] = {}
    for target, event in sorted(latest_by_target(read_events(project)).items()):
        if not _carries(target, event.kind.value):
            continue
        if event.scope.kind is not ScopeKind.PROJECT or " below " in target:
            left_out[SCOPED_TO_A_FOLDER] = left_out.get(SCOPED_TO_A_FOLDER, 0) + 1
            continue
        try:
            payload = json.loads(event.payload or "{}")
        except ValueError:
            payload = {}
        kept.append(
            CarriedAnswer(
                target=target,
                kind=event.kind.value,
                payload=payload if isinstance(payload, dict) else {},
                actor=event.actor,
                occurred_at=event.occurred_at,
                source=source,
            )
        )
    return CarriedWords(answers=tuple(kept), left_out=left_out)


def record_file(words: CarriedWords, *, source: str) -> dict[str, Any]:
    """A file of Record events carrying ``words`` between machines, canonical and deterministic.

    ``source`` is the name of the project they were given in; the file says what it is
    (:data:`RECORD_FORMAT`) so Import knows, and carries no time of its own.
    """
    return {
        "format": RECORD_FORMAT,
        "version": RECORD_FORMAT_VERSION,
        "from": source,
        "events": [answer.as_json() for answer in words.answers],
        "left_out": dict(sorted(words.left_out.items())),
    }


def read_record_file(document: Any) -> CarriedWords:
    """The answers a file of Record events holds, refused in words where it is not one.

    ``document`` is the parsed JSON or its text. A file of another shape is refused whole, and an
    event naming a target that is not a person's words is refused with it: nothing is guessed.
    """
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError as error:
            message = f"the file is not JSON, so it holds no Record events: {error}"
            raise ImportRefusedError(message) from error
    if not isinstance(document, dict) or document.get("format") != RECORD_FORMAT:
        message = f"a file of Record events says so: its format is {RECORD_FORMAT!r}"
        raise ImportRefusedError(message)
    if document.get("version") != RECORD_FORMAT_VERSION:
        message = f"this file is version {document.get('version')!r}; this engine reads version 1"
        raise ImportRefusedError(message)
    held = document.get("events")
    if not isinstance(held, list):
        message = "a file of Record events lists its events"
        raise ImportRefusedError(message)
    from ingence.core.schema import RecordEventKind

    answers: list[CarriedAnswer] = []
    for item in held:
        if not isinstance(item, dict):
            message = "each event in the file is an object"
            raise ImportRefusedError(message)
        target, kind = str(item.get("target") or ""), str(item.get("kind") or "")
        if kind not in {found.value for found in RecordEventKind} or not _carries(target, kind):
            message = f"an event on {target!r} of kind {kind!r} is not one a person's words carry"
            raise ImportRefusedError(message)
        payload = item.get("payload")
        answers.append(
            CarriedAnswer(
                target=target,
                kind=kind,
                payload=dict(payload) if isinstance(payload, dict) else {},
                actor=str(item.get("actor") or ""),
                occurred_at=str(item.get("occurred_at") or ""),
                source=str(item.get("from") or ""),
            )
        )
    left = document.get("left_out")
    counted = (
        {str(name): int(count) for name, count in left.items()} if isinstance(left, dict) else {}
    )
    return CarriedWords(answers=tuple(answers), left_out=counted)


@dataclass
class CarryReport:
    """What carrying answers into a project did, answer by answer."""

    written: list[Any] = field(default_factory=list)
    #: Targets that already hold an answer in force, which keeps its place.
    kept: list[str] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)


#: Payload keys that describe the project an answer was given in, which mean nothing in another.
_DESCRIBES_ITS_PROJECT = ("as_shown", "corrects", "source")


def _naming(answer: CarriedAnswer) -> tuple[str, str, str] | None:
    """What a carried meaning names: the spelling, the term it means and that term's kind.

    None for any other carried answer (a split, a pair called unrelated, a "not a word").
    """
    prefix, _, spelled = answer.target.partition(":")
    if prefix != "term" or answer.kind not in ("define", "assign"):
        return None
    term = str(answer.payload.get("answer") or "").strip()
    kind = str(answer.payload.get("theme_kind") or "").strip()
    return (spelled, term, kind) if spelled and term and kind else None


def _naming_refusal(
    project: Path, store: DictionaryStore, named: tuple[str, str, str], known: Mapping[str, str]
) -> str | None:
    """Why a carried meaning is not written here, in words, or None where it can be.

    The checks are the ones that the Words section makes when a person names a meaning
    (``define_word``). A term that this project already holds on another kind of thing is refused,
    so a term is never two kinds at once. A meaning, or a spelling of it, that this project's person
    turned off is refused too. ``known`` is the terms (with their kinds) that the carried answers
    already written in this call have named.
    """
    from ingence.dictionary.inactive import inactive_refusal

    spelled, term, axis = named
    held = store.sense_for_term(term)
    held_kind = held.theme_kind if held is not None else known.get(term)
    if held_kind is not None and held_kind != axis:
        return (
            f"{term!r} is already a sense on the {held_kind!r} axis in this project, and the "
            f"carried answer puts it on {axis!r}"
        )
    return inactive_refusal(project, spelling=spelled, term=term)


def apply_carried(
    project: Path,
    words: CarriedWords,
    *,
    statement: ImportStatement,
    imported_from_name: str,
    occurred_at: str,
) -> CarryReport:
    """Write ``words`` into ``project``'s Record as imported events, and say what became of each.

    Each answer is written as the person bringing it in (``statement.author``), against the run that
    the project's pointer names now. The ``source`` names the project it came from, and the payload
    holds the original author and moment. A target that already holds a decision in force keeps it,
    because an import never writes over an answer, and the target is listed as kept. A project with
    no published run is refused whole, in words, because an event is made against a run.
    """
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.dictionary.store import DictionaryStore
    from ingence.record.log import AppendError, append_event, latest_by_target, read_events
    from ingence.storage.commit import read_pointer

    pointer = read_pointer(project)
    if pointer is None:
        message = "this project has no run yet: read it first, then bring the words in"
        raise ImportRefusedError(message)
    in_force = latest_by_target(read_events(project))
    report = CarryReport()
    store: DictionaryStore | None = None
    known: dict[str, str] = {}
    for answer in words.answers:
        if answer.target in in_force:
            report.kept.append(answer.target)
            continue
        named = _naming(answer)
        if named is not None:
            # One Dictionary for the whole carry, loaded when the first meaning needs it.
            store = store if store is not None else DictionaryStore.for_project(project)
            why = _naming_refusal(project, store, named, known)
            if why is not None:
                report.refused.append({"target": answer.target, "error": why})
                continue
        payload = {
            key: value for key, value in answer.payload.items() if key not in _DESCRIBES_ITS_PROJECT
        }
        payload["source"] = {
            "page": "words",
            "imported_from": answer.source or imported_from_name,
            "carried": True,
            **statement.source(),
        }
        payload["carried_from"] = {"actor": answer.actor, "occurred_at": answer.occurred_at}
        try:
            report.written.append(
                append_event(
                    project,
                    kind=RecordEventKind(answer.kind),
                    actor=statement.author,
                    occurred_at=occurred_at,
                    target=answer.target,
                    against_run_id=pointer.run_id,
                    payload=payload,
                    scope=PROJECT_SCOPE,
                )
            )
        except AppendError as error:
            report.refused.append({"target": answer.target, "error": str(error)})
            continue
        if named is not None:
            known[named[1]] = named[2]
    return report
