"""Answering a question in the Review view: the one place the API writes.

``views.py`` promises it computes nothing, and ``review.py`` promises it stores nothing. This
module writes, and it is a separate module so that those promises stay testable and do not
become approximately true. It follows the precedent of ``infer/claims.py`` being the one place a
claim is built, and of ``rulebook/load.py`` being the one place a rule is.

An answer becomes a Record event and nothing else. It publishes no claim, edits no Dictionary
file and changes no rule. :func:`~ingence.record.questions.answers_in_force` reads it back.
:mod:`ingence.dictionary.answers` turns it into a ``USER``-origin sense at precedence 40, at the
scope the answer carried. Nothing is written out beside the Record, because a second copy of a
decision is the copy that drifts.

The kind travels with the answer, and that is what makes it readable. A sense on no axis is a
sense no rule reads, because every rule that reads a sense reads an axis. For an assignment the
queue already knows the kind, from the sense it offered, and records it without asking. For a
definition only the person knows the kind, so it is required and refused where it is missing.
That is always the case for a region question, since a question whose inputs are components and
not bindings offers no sense to assign.

The decision is about the question, not about one file. A person who answers "``sd`` means
standard deviation" has not decided something about the fourth file in a list. They have decided
what the term means, wherever the engine was stuck on it. So the target is the question, spelled
canonically by :func:`question_target`. The payload carries what the queue was showing when they
answered, which is how many files and how many blocked conclusions. A later reader can then see
the decision in the light of what it was worth at the time.

This is attribution and not authentication. The engine runs on one person's machine with no
accounts, so the actor is what the person writing the decision says it is. The Record guarantees
that every decision has an actor, a moment, a run and a subject, that the engine invented none of
it, and that nothing can later be rewritten. It does not and cannot check who somebody is.

A stale page cannot write. An answer names a question, and the route refuses a question that the
current run does not raise. Otherwise a page left open across an ``ingence infer`` would write
decisions about findings that no longer exist. That is the one way an append-only log can be made
to hold nonsense without anybody editing it.

A verdict on a proposed pair comes through the same route. The Candidates view says ``derived``,
``reversed``, ``unrelated`` or ``cannot_say`` about one pair. Each word decides what the one table
in ``record/verdicts.py`` says it decides, and the event's target is ``candidate:<candidate_id>``.
The same refusals hold: an actor is required, and a pair that the current run does not propose is
a page out of date, answered with a conflict.

Every review sheet's kind is a page that writes. :func:`decide_target` takes a row by the target
that the Record spells for it (a question, a class, a claim, a pair, an extension, a word, a
folder). It writes the same event that the sheet import writes for that kind, through the same
functions (``record/verdicts.py``). So a verdict given on the page and the same verdict imported
from a sheet differ only in where each says it came from. A row takes a note on its own, which is
a ``note`` event and never the decision in force. A file of answers imported from the page
(``api/routes/imports.py``) is written here too, each answer as its author's, with the event's
source saying the file and who imported it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ingence.api.routes.proposals import GROUP_PREFIX, MISCELLANEOUS_PREFIX, PACK_PREFIX
from ingence.api.routes.review import review
from ingence.core.ids import PROJECT_SCOPE, Scope, ScopeKind
from ingence.core.schema import DictionaryOrigin, RecordEventKind
from ingence.record.log import append_event, latest_by_target, read_events
from ingence.record.questions import TERM_PREFIX, question_target, term_target
from ingence.record.targets import (
    ANALYSED_ELSEWHERE_PREFIX,
    CANDIDATE_PREFIX,
    CLAIM_PREFIX,
    CLASS_PREFIX,
    DISTINCT_PREFIX,
    EXTENSION_PREFIX,
    FOLDER_PREFIX,
    QUESTION_PREFIX,
    SPLIT_PREFIX,
    THEME_PREFIX,
    class_target,
    distinct_named_by,
    distinct_target,
    extension_named_by,
    extension_target,
)
from ingence.record.verdicts import (
    CANDIDATE_WORDS,
    CLAIM_WORDS,
    VERDICTS,
    WORTH_READING,
    Subject,
    VerdictRefusedError,
    candidate_subjects,
    claim_subjects,
    class_index,
    class_shown,
    class_takes,
    current_run,
    extension_counts,
    extension_event,
    question_subject,
    question_takes,
    question_words,
    write_note,
    write_observation,
    write_verdict,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.dictionary.store import DictionaryStore
    from ingence.storage.catalog import Catalog

__all__ = [
    "ANSWER_KINDS",
    "COMPUTER_READINGS",
    "COVERAGE_SOURCE",
    "NOT_A_WORD",
    "PAGES",
    "AlreadyAnsweredError",
    "AnswerRefusedError",
    "CandidateMovedError",
    "Decision",
    "DecisionContext",
    "PreparedAnswer",
    "QuestionMovedError",
    "annotate_folder",
    "answer_candidate",
    "answer_question",
    "decide_many",
    "decide_target",
    "define_word",
    "prepare_answer",
    "question_target",
    "recorded_answers",
    "say_folder_is_program",
    "write_prepared",
]

#: Where the Coverage view's own writes say they came from.
COVERAGE_SOURCE = "coverage view"

#: What a person says of a word that is not one -- an identifier, a digest, a fragment of code.
#: A rejection: the rule raised a word to be defined, and there is none to define.
NOT_A_WORD = "not_a_word"
#: The word for "I cannot tell": on a question a decision not to decide. On a proposal, a note.
CANNOT_SAY = "cannot_say"

#: What a string that is not a person's word is to a computer. A person who answers "not a word"
#: says which of these it is, because "not a word" is a human response about whether something
#: is a human word. Many things are shaped like words and mean something to a computer without
#: meaning something to a human: extensions, signifiers, even letter combinations. The Record
#: keeps the answer beside the rejection as ``reading``: an extension written inside a name, a
#: code or identifier, a mark a program writes, or letters standing for nothing. Each is still
#: :data:`NOT_A_WORD`, with nothing to define, and says more than that alone did.
COMPUTER_READINGS = ("an_extension", "a_code", "a_program_mark", "just_letters")

#: The page a class of claims is answered on: the Questions page's sheet.
CLAIM_CLASS_PAGE = "questions"

#: The page each kind of row is decided on, as a decision's ``source.page`` names it.
PAGES: dict[str, str] = {
    QUESTION_PREFIX: "review",
    CLASS_PREFIX: "groupings",
    CLAIM_PREFIX: "claim samples",
    CANDIDATE_PREFIX: "proposed pairs",
    EXTENSION_PREFIX: "extensions",
    TERM_PREFIX: "words",
    FOLDER_PREFIX: "folders",
    # The two proposals are answered on the Questions page.
    PACK_PREFIX: "review",
    MISCELLANEOUS_PREFIX: "review",
    GROUP_PREFIX: "groupings",
    # A person's answer that an experiment's analysis is elsewhere, given where its experiments
    # are listed.
    ANALYSED_ELSEWHERE_PREFIX: "experiments",
    # The Words section: how a spelling that a capital divides reads, and two spellings that a
    # person says are unrelated.
    SPLIT_PREFIX: "words",
    DISTINCT_PREFIX: "words",
}


class AnswerRefusedError(Exception):
    """The answer was not written, and the message says what was wrong with it."""


class QuestionMovedError(AnswerRefusedError):
    """The question is not in the current run, so a page is answering something gone.

    Its own class because it is the one refusal a page can act on by itself: everything else
    means the answer was wrong, and this means the page is out of date. The route turns it
    into a conflict rather than a bad request for that reason.
    """


class AlreadyAnsweredError(AnswerRefusedError):
    """One answer over a listed set met a row that was answered since the list was shown.

    The row keeps the answer in force on it, because a list read before somebody answered is not
    a reason to overwrite what they said. The caller counts it apart, as already answered.
    """


class CandidateMovedError(QuestionMovedError):
    """The pair is not proposed by the current run: the same refusal, about a candidate."""


#: What a person may do with a question, and what each means when the Record is replayed.
#: A subset of :class:`RecordEventKind`: ``merge`` and ``split`` are about classes rather
#: than about a question, and ``override`` is about a rule's parameters, which is the
#: Rulebook view's write and not this one.
ANSWER_KINDS: dict[RecordEventKind, str] = {
    RecordEventKind.ASSIGN: (
        "the term means one of the senses the engine already knows, and this is which"
    ),
    RecordEventKind.DEFINE: (
        "the term means something the Dictionary does not hold, stated in the answer"
    ),
    RecordEventKind.CONFIRM: "what the rule raised is right, and needs no further reading",
    RecordEventKind.REJECT: "what the rule raised is wrong here",
    RecordEventKind.ANNOTATE: "not decided, and here is what a person wants remembered",
    RecordEventKind.NOTE: "a person's words about it, deciding nothing and displacing nothing",
}


def current_questions(catalog: Catalog) -> list[dict[str, Any]]:
    """Return every question the current run raises, as the queue builds them, unranked.

    A set-aside question is still a question that the run raises, so it may be answered. The
    queue does not ask it, and a person who answers it anyway has decided something real.
    """
    page = review(catalog, limit=1_000_000, sample=0)
    questions: list[dict[str, Any]] = [*page["questions"], *page["set_aside"]]
    return questions


def _question(
    catalog: Catalog,
    rule_id: str,
    raised: str,
    questions: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Find the question in the current run, or refuse.

    The function reads through the queue and not from the findings table directly, so that what
    may be answered is exactly what a person could have been shown. A question that the queue
    does not produce is one that nobody could have been looking at. A caller that answers many
    questions from one run passes the queue it read once.
    """
    if questions is None:
        questions = current_questions(catalog)
    for item in questions:
        if item["rule_id"] == rule_id and item["raised"] == raised:
            return item
    message = (
        f"the current run raises no question {raised!r} from {rule_id!r}: it is no longer "
        "asked (a later run answered it, or no longer raises it)."
    )
    raise QuestionMovedError(message)


def _refuse_inactive(project: Path, spelling: str, term: str) -> None:
    """Refuse an answer naming a meaning, or a spelling of it, that a person inactivated."""
    from ingence.dictionary.inactive import inactive_refusal

    why = inactive_refusal(project, spelling=spelling, term=term)
    if why is not None:
        raise AnswerRefusedError(why)


def _current_run(catalog: Catalog) -> str:
    row = catalog.query("SELECT run_id FROM run").fetchone()
    if not row or not row[0]:
        message = "the project has no published run, so there is nothing to decide against"
        raise AnswerRefusedError(message)
    return str(row[0])


def _offered_kind(question: dict[str, Any], term: str) -> str:
    """The axis the question says a sense it offers sits on.

    The function reads it off the question and not from the Dictionary, because what the person
    was shown is what they answered, and the queue built that from the run's own vocabulary
    snapshot. A sense that the snapshot holds under an identifier it cannot name reaches here
    with an empty kind. The caller refuses an assignment to it for the same reason it refuses a
    definition with none.
    """
    for sense in question["senses"]:
        if str(sense["term"]) == term:
            return str(sense.get("theme_kind") or "")
    return ""


@dataclass(frozen=True, slots=True)
class PreparedAnswer:
    """One answer to a question, validated and ready to write: everything but the writing.

    A check and a write share one validation path, so a sheet that the check reports as acceptable
    is one that the write accepts. Both prepare here, and only :func:`write_prepared` differs.
    """

    kind: RecordEventKind
    target: str
    against_run_id: str
    payload: dict[str, Any]
    scope: Scope


def write_prepared(
    project: Path, prepared: PreparedAnswer, *, actor: str, occurred_at: str
) -> RecordEvent:
    """Append a prepared answer to the Record and return the event as stored."""
    return append_event(
        project,
        kind=prepared.kind,
        actor=actor,
        occurred_at=occurred_at,
        target=prepared.target,
        against_run_id=prepared.against_run_id,
        payload=prepared.payload,
        scope=prepared.scope,
    )


def answer_question(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    rule_id: str,
    raised: str,
    kind: RecordEventKind,
    answer: str | None = None,
    theme_kind: str | None = None,
    note: str | None = None,
    scope: Scope = PROJECT_SCOPE,
    questions: Sequence[dict[str, Any]] | None = None,
    source: dict[str, Any] | None = None,
    reading: str | None = None,
) -> RecordEvent:
    """Write one answer to the Record and return the event as stored.

    :func:`prepare_answer` and :func:`write_prepared`, in one call; see the first for what is
    refused and why.
    """
    prepared = prepare_answer(
        catalog,
        rule_id=rule_id,
        raised=raised,
        kind=kind,
        answer=answer,
        theme_kind=theme_kind,
        note=note,
        scope=scope,
        questions=questions,
        source=source,
        reading=reading,
    )
    return write_prepared(project, prepared, actor=actor, occurred_at=occurred_at)


def prepare_answer(
    catalog: Catalog,
    *,
    rule_id: str,
    raised: str,
    kind: RecordEventKind,
    answer: str | None = None,
    theme_kind: str | None = None,
    note: str | None = None,
    scope: Scope = PROJECT_SCOPE,
    questions: Sequence[dict[str, Any]] | None = None,
    source: dict[str, Any] | None = None,
    reading: str | None = None,
) -> PreparedAnswer:
    """Validate one answer to a question and return what writing it would append.

    ``answer`` is required for ``assign`` and ``define``, and for ``assign`` it must be one of the
    senses the question offers. ``confirm``, ``reject`` and ``annotate`` take none.

    ``theme_kind`` is the axis the named sense sits on, and it is what makes the answer readable:
    a sense on no axis is a sense no rule reads. An assignment takes it from the sense the
    question offered, and the function refuses any other value, because the engine knows that
    one and a person who contradicts it is answering a different question. A definition must
    state it, because nothing else can.
    """
    if kind not in ANSWER_KINDS:
        allowed = ", ".join(sorted(item.value for item in ANSWER_KINDS))
        message = f"{kind.value!r} is not something a question can be answered with: {allowed}"
        raise AnswerRefusedError(message)

    # Before the question, so a project with no run says that rather than reporting a
    # question missing from a queue it never had.
    against_run = _current_run(catalog)
    chosen = (answer or "").strip()
    if kind in (RecordEventKind.ASSIGN, RecordEventKind.DEFINE) and chosen:
        _refuse_inactive(catalog.project, raised, chosen)
    question = _question(catalog, rule_id, raised, questions)
    offered = {str(sense["term"]) for sense in question["senses"]}

    axis = (theme_kind or "").strip()
    if kind is RecordEventKind.ASSIGN:
        if not chosen:
            message = "an assignment names the sense it assigns; assigning nothing decides nothing"
            raise AnswerRefusedError(message)
        if chosen not in offered:
            names = ", ".join(sorted(offered)) or "none"
            message = (
                f"{chosen!r} is not a sense this question offers. It offers: {names}. A sense "
                "the engine does not hold is a definition rather than an assignment, so say "
                "define instead and the Record will carry it as new vocabulary."
            )
            raise AnswerRefusedError(message)
        known = _offered_kind(question, chosen)
        if axis and axis != known:
            message = (
                f"this question offers {chosen!r} on the {known!r} axis, and {axis!r} was "
                f"given. An assignment takes the axis the engine already holds; naming a "
                f"different one is a definition of another sense under the same name."
            )
            raise AnswerRefusedError(message)
        axis = known
    elif kind is RecordEventKind.DEFINE:
        if not chosen:
            message = "a definition states what the term means; defining nothing decides nothing"
            raise AnswerRefusedError(message)
        if not axis:
            message = (
                'a meaning names its kind, written "meaning [kind]", and none was given; '
                "without one no rule can read it, so nothing was written"
            )
            raise AnswerRefusedError(message)
    elif kind is RecordEventKind.NOTE and not (note or "").strip():
        message = "a note says something; an empty note decides nothing and records nothing"
        raise AnswerRefusedError(message)
    if reading is not None and kind is not RecordEventKind.REJECT:
        message = "what a string is to a computer is said only beside 'not a word'"
        raise AnswerRefusedError(message)
    if kind not in (RecordEventKind.ASSIGN, RecordEventKind.DEFINE) and chosen:
        # The Rulebook loader refuses a rule whose sentence never mentions a parameter it
        # sets, because a setting nobody reading the rule can see is worse than none. The
        # same applies to a decision: a rejection carrying an assigned sense reads, to
        # anybody later, as though the person had assigned it.
        message = (
            f"a {kind.value} takes no answer, and {chosen!r} was given. Only an assignment "
            "or a definition names a sense; a rejection that carried one would read as an "
            "assignment to whoever replays the Record."
        )
        raise AnswerRefusedError(message)

    # What the queue was showing when the person answered. A decision made while four
    # thousand files turned on it is different evidence from the same decision made while
    # two did, and only the Record can say which this was.
    payload: dict[str, Any] = {
        "rule_id": rule_id,
        # The version the question was raised under, so a precision figure counts a decision
        # against the rule it was about and not a later one.
        "rule_version": str(question.get("rule_version") or ""),
        "raised": raised,
        "as_shown": {
            "entries_affected": question["entries_affected"],
            "conclusions_blocked": question["conclusions_blocked"],
            "senses_offered": question["senses_offered"],
        },
    }
    if chosen:
        payload["answer"] = chosen
        payload["theme_kind"] = axis
    if kind is RecordEventKind.REJECT and reading is not None:
        payload["verdict"] = NOT_A_WORD
        payload["reading"] = _computer_reading(reading)
    if note and note.strip():
        payload["note"] = note.strip()
    if source:
        payload["source"] = dict(source)

    return PreparedAnswer(
        kind=kind,
        target=question_target(rule_id, raised),
        against_run_id=against_run,
        payload=payload,
        scope=scope,
    )


def answer_candidate(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    candidate_id: str,
    verdict: str,
    note: str | None = None,
) -> RecordEvent:
    """Write one verdict on a proposed pair to the Record and return the event as stored.

    ``verdict`` is one of :data:`~ingence.record.verdicts.CANDIDATE_WORDS`. What each decides
    is ``record/verdicts.py``'s table, and the payload carries the word, the rule, both ends
    and the tier the pair was proposed at, so the decision reads without the run.
    """
    word = (verdict or "").strip()
    if word not in CANDIDATE_WORDS:
        allowed = ", ".join(CANDIDATE_WORDS)
        message = f"{word!r} is not something a proposed pair can be answered with: {allowed}"
        raise AnswerRefusedError(message)
    try:
        against_run = current_run(catalog)
    except VerdictRefusedError as error:
        raise AnswerRefusedError(str(error)) from error
    subject = candidate_subjects(catalog, [candidate_id]).get(candidate_id)
    if subject is None:
        message = (
            f"the current run does not propose the pair {candidate_id!r}: it is no longer "
            "proposed (a later run answered it, or no longer proposes it)."
        )
        raise CandidateMovedError(message)
    try:
        return write_verdict(
            project,
            subject,
            word,
            actor=actor,
            occurred_at=occurred_at,
            against_run_id=against_run,
            note=note,
        )
    except VerdictRefusedError as error:
        raise AnswerRefusedError(str(error)) from error


def answer_extension(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    extension: str,
    worth_reading: str,
    note: str | None = None,
) -> RecordEvent:
    """Write what a person said about reading one extension's files (the coverage view).

    The event is the same one that a row of the extensions sheet becomes: the word's kind from
    the one table (``WORTH_READING``), the extension as the target, the word in the payload, and
    what the run held when the person answered. The coverage view offers it beside an extension
    that nobody has taught the engine, so the sheet becomes a control. The function refuses an
    extension of which the current run holds no file, because a person who answers about files
    they cannot see is answering about nothing.
    """
    from ingence.core.extensions import canonical_extension
    from ingence.record.targets import extension_target
    from ingence.record.verdicts import WORTH_READING

    spelled = canonical_extension(extension)
    if worth_reading not in WORTH_READING:
        allowed = ", ".join(sorted(WORTH_READING))
        message = f"{worth_reading!r} is not a worth-reading answer: {allowed}"
        raise AnswerRefusedError(message)
    from ingence.storage.own_files import filesystem_entry

    # The files of the extension a person sees, never a part recovered from inside one, so the
    # count stored with the answer agrees with the count the page showed.
    held = catalog.query(
        "SELECT count(*), coalesce(sum(size_bytes), 0) FROM entries "  # noqa: S608 -- a constant
        f"WHERE entry_type = 'file' AND NOT is_deleted AND {filesystem_entry()} "
        "AND lower(extension) = ?",
        [spelled],
    ).fetchone()
    files, size = (int(held[0]), int(held[1])) if held else (0, 0)
    if files == 0:
        message = f"the current run holds no file with the extension {spelled!r}"
        raise AnswerRefusedError(message)
    return append_event(
        project,
        kind=WORTH_READING[worth_reading],
        actor=actor,
        occurred_at=occurred_at,
        target=extension_target(spelled),
        against_run_id=_current_run(catalog),
        payload={
            "extension": spelled,
            "worth_reading": worth_reading,
            "as_shown": {"files": files, "bytes": size},
            "source": "coverage view",
            **({"note": note} if note else {}),
        },
        scope=PROJECT_SCOPE,
    )


def _folder_scope(catalog: Catalog, below: str | None) -> tuple[str | None, Scope]:
    """The folder a meaning is kept below, and the scope that is: the project's where none is named.

    Refused in words where the current run holds no such folder.
    """
    folder = (below or "").strip().strip("/") or None
    if folder is None:
        return None, PROJECT_SCOPE
    found = catalog.query(
        "SELECT count(*) FROM entries WHERE relative_path = ? AND entry_type = 'directory'",
        [folder],
    ).fetchone()
    if not found or not int(found[0]):
        message = f"the current run holds no folder {folder!r} to keep the meaning below"
        raise AnswerRefusedError(message)
    return folder, Scope(ScopeKind.FOLDER, folder)


def define_word(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    word: str,
    answer: str,
    theme_kind: str,
    note: str | None = None,
    scope: Scope = PROJECT_SCOPE,
    below: str | None = None,
    source: str | Mapping[str, Any] = COVERAGE_SOURCE,
    store: DictionaryStore | None = None,
    known_terms: Mapping[str, str] | None = None,
) -> RecordEvent:
    """Write what a person said a word means where no rule asked.

    The Coverage view lists the words that bind to nothing. The one route that writes answers
    questions, and a word that nobody contests raises none. This function lets a person say
    something about such a word. The target is the word itself (``term:<word>``). The event is an
    assignment where the person names a sense that the Dictionary already holds on that axis, and
    a definition otherwise. The next ``index`` or ``index --rebind`` binds it at ``USER``
    precedence, exactly as an answered question does.

    The function refuses a word that the Dictionary already places, because then the word is
    contested or settled, and the Review view's question is where a person answers it. It also
    refuses a word that no name in the current run carries, because a person who defines a word
    that no file uses is defining nothing here.

    A batch uses one Dictionary. A caller that answers many words at once hands over the ``store``
    it loaded once, and the terms (with their kinds) it has written itself in this batch as
    ``known_terms``. So a word assigned to a term that another word of the batch defined is an
    assignment to it, and not a second definition.

    ``below`` gives a folder. A word that the Dictionary does not place is then given a meaning
    for the names under that folder alone, on ``term:<word> below <folder>`` as a correction is.
    So a batch answer that names a folder is kept below it and never for the whole project.
    """
    from ingence.dictionary.store import DictionaryStore

    spelled = word.strip()
    named = answer.strip()
    axis = theme_kind.strip()
    if not (spelled and named and axis):
        message = "a word, the sense it means, and the axis that sense sits on are all needed"
        raise AnswerRefusedError(message)
    _refuse_inactive(project, spelled, named)
    folder, folded = _folder_scope(catalog, below)
    if folder is not None:
        scope = folded
    files = _names_carrying(catalog, spelled, folder)
    if files == 0:
        where = f" below {folder}" if folder is not None else ""
        message = f"no name in the current run carries the word {spelled!r}{where}"
        raise AnswerRefusedError(message)
    store = store if store is not None else DictionaryStore.for_project(project)
    try:
        placed = store.look_up(spelled, scope=scope)
    except ValueError:  # a root-scoped sense and a folder: undecidable, so nothing is settled
        placed = None
    if placed is not None:
        message = (
            f"the Dictionary already places {spelled!r}; where it is contested the Questions page "
            "asks about it, and an answer there is how its meaning is changed"
        )
        raise AnswerRefusedError(message)
    existing = store.sense_for_term(named)
    held_kind = existing.theme_kind if existing is not None else (known_terms or {}).get(named)
    if held_kind is not None and held_kind != axis:
        message = (
            f"{named!r} is already a sense on the {held_kind!r} axis, and {axis!r} was "
            "given; name that axis to assign it, or another name to define a new sense"
        )
        raise AnswerRefusedError(message)
    return append_event(
        project,
        kind=RecordEventKind.ASSIGN if held_kind is not None else RecordEventKind.DEFINE,
        actor=actor,
        occurred_at=occurred_at,
        target=correction_target(spelled.casefold(), folder),
        against_run_id=_current_run(catalog),
        payload={
            "raised": spelled.casefold(),
            "answer": named,
            "theme_kind": axis,
            "as_shown": {"files": files},
            # Below a folder the sense carries every spelling the names there write (as a
            # correction's does): a loose match is tried after an exact one.
            **({"spellings": _spellings_of(catalog, spelled, folder)} if folder else {}),
            "source": source if isinstance(source, str) else dict(source),
            **({"note": note} if note else {}),
        },
        scope=scope,
    )


def _names_carrying(catalog: Catalog, spelled: str, below: str | None) -> int:
    """How many entries' names carry a word in the current run, anywhere or below one folder."""
    if below is None:
        held = catalog.query(
            "SELECT count(DISTINCT entry_id) FROM components WHERE lower(normalized_value) = ?",
            [spelled.casefold()],
        ).fetchone()
    else:
        held = catalog.query(
            """
            SELECT count(DISTINCT c.entry_id) FROM components c
            JOIN entries e ON e.entry_id = c.entry_id
            WHERE lower(c.normalized_value) = ?
              AND (e.relative_path = ? OR starts_with(e.relative_path, ? || '/'))
            """,
            [spelled.casefold(), below, below],
        ).fetchone()
    return int(held[0]) if held else 0


def _spellings_of(catalog: Catalog, spelled: str, below: str | None) -> list[str]:
    """Every spelling the run's names write a word in, other than its lower-case one.

    A correction's sense carries them all. A shipped surface written in capitals (``RPM``)
    matches a name written so exactly, and an exact match is tried before a loose one. A sense
    spelled only in lower case would not reach those names.
    """
    folder = "" if below is None else below
    rows = catalog.query(
        """
        SELECT DISTINCT spelling FROM (
            SELECT c.raw_value AS spelling, e.relative_path AS path FROM components c
            JOIN entries e ON e.entry_id = c.entry_id WHERE lower(c.normalized_value) = ?
            UNION ALL
            SELECT c.normalized_value, e.relative_path FROM components c
            JOIN entries e ON e.entry_id = c.entry_id WHERE lower(c.normalized_value) = ?
        )
        WHERE spelling IS NOT NULL
          AND (? = '' OR path = ? OR starts_with(path, ? || '/'))
        ORDER BY spelling
        """,
        [spelled.casefold(), spelled.casefold(), folder, folder, folder],
    ).fetchall()
    return sorted({str(row[0]) for row in rows if str(row[0]) != spelled.casefold()})


def correction_target(word: str, below: str | None) -> str:
    """The Record target of a correction: the word's, and the folder it is kept below.

    There is one target per word and scope, because only the latest decision on a target is in
    force. A correction below a folder, written on the word's own target, would replace the
    person's meaning for the whole project. So ``term:<word>`` holds the project-wide meaning (the
    target that an unbound word's definition uses), and ``term:<word> below <folder>`` holds the
    meaning below one folder. A later correction at the same scope replaces the earlier there.
    """
    return term_target(word) if below is None else f"{term_target(word)} below {below}"


def correct_word(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    word: str,
    answer: str,
    theme_kind: str,
    below: str | None = None,
    note: str | None = None,
    source: str | Mapping[str, Any] = COVERAGE_SOURCE,
    store: DictionaryStore | None = None,
    known_terms: Mapping[str, str] | None = None,
) -> RecordEvent:
    """Write a person's own meaning for a word that the Dictionary already settles.

    Without this, only a word that nothing binds (:func:`define_word`) or a contested one (a
    question) could be given a meaning. A correction is the same event on the same target,
    ``term:<word>``: a definition, or an assignment where the named sense is one that the
    Dictionary already holds on that axis. It has a scope: the whole project, or ``below`` one
    folder (its path as the census writes it) and everything beneath it. The Dictionary reads it
    back as a ``USER`` sense, which outranks the shipped vocabulary and every pack, so the next
    rebind binds it. The server's applier starts that rebind at once, as it does for an unbound
    word's definition.

    The shipped vocabulary is not changed. A pack still says what it says, and the correction is
    a person's, in their Record, with what it corrected kept beside it (``corrects``). The
    function refuses a word that no name carries (there, below the folder), a folder the run does
    not hold, a sense already named on another axis, and a correction that says what the
    Dictionary already says. ``store`` and ``known_terms`` are a batch's (see
    :func:`define_word`).
    """
    from ingence.dictionary.store import DictionaryStore

    spelled = word.strip()
    named = answer.strip()
    axis = theme_kind.strip()
    if not (spelled and named and axis):
        message = "a word, the meaning you give it, and what kind of thing that is are all needed"
        raise AnswerRefusedError(message)
    _refuse_inactive(project, spelled, named)
    folder, scope = _folder_scope(catalog, below)
    files = _names_carrying(catalog, spelled, folder)
    if files == 0:
        where = f" below {folder}" if folder is not None else ""
        message = f"no name in the current run carries the word {spelled!r}{where}"
        raise AnswerRefusedError(message)
    store = store if store is not None else DictionaryStore.for_project(project)
    existing = store.sense_for_term(named)
    held_kind = existing.theme_kind if existing is not None else (known_terms or {}).get(named)
    if held_kind is not None and held_kind != axis:
        message = (
            f"{named!r} is already a meaning of the kind {held_kind!r}, and {axis!r} "
            "was given; choose that kind to use it, or another name for a meaning of your own"
        )
        raise AnswerRefusedError(message)
    try:
        hit = store.look_up(spelled, scope=scope)
    except ValueError:  # a root-scoped sense and a folder: undecidable, so nothing is settled
        hit = None
    settled = hit if hit is not None and not hit.ambiguous else None
    if settled is not None and (settled.sense.term, settled.sense.theme_kind) == (named, axis):
        message = (
            f"{spelled!r} already means {named} ({axis.replace('_', ' ')}) there; "
            "nothing to correct"
        )
        raise AnswerRefusedError(message)
    corrects = (
        {
            "term": settled.sense.term,
            "theme_kind": settled.sense.theme_kind,
            "origin": settled.sense.origin.value,
            "section": settled.sense.section,
            "scope": settled.sense.scope.token,
        }
        if settled is not None
        else None
    )
    target = correction_target(spelled.casefold(), folder)
    answered: dict[str, str] = {}
    # A person's answer to a question is replaced, not tied with. Two of a person's senses at one
    # scope stand level, and the word would read as neither. Where the meaning in force is a
    # person's answer to a question at this scope, the correction is written on that question's
    # target, where it takes the answer's place.
    raised_by = str(settled.sense.properties.get("raised_by") or "") if settled is not None else ""
    if (
        settled is not None
        and settled.sense.origin is DictionaryOrigin.USER
        and raised_by
        and settled.sense.scope == scope
    ):
        surface = settled.sense.surfaces[0] if settled.sense.surfaces else spelled.casefold()
        target = question_target(raised_by, surface)
        answered = {"rule_id": raised_by, "raised": surface}
    return append_event(
        project,
        kind=RecordEventKind.ASSIGN if held_kind is not None else RecordEventKind.DEFINE,
        actor=actor,
        occurred_at=occurred_at,
        target=target,
        against_run_id=_current_run(catalog),
        payload={
            "raised": spelled.casefold(),
            "answer": named,
            "theme_kind": axis,
            "as_shown": {"files": files},
            "spellings": _spellings_of(catalog, spelled, folder),
            "corrects": corrects,
            "source": source if isinstance(source, str) else dict(source),
            **answered,
            **({"note": note} if note else {}),
        },
        scope=scope,
    )


def annotate_folder(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    entry_id: str,
    described: str | None = None,
    note: str | None = None,
    source: str | Mapping[str, Any] = COVERAGE_SOURCE,
) -> RecordEvent:
    """Write what a person says a folder holds, from any row naming one.

    The event is the same one that a folder row of a review sheet becomes: an annotation, the
    folder as the target, what the person said in the payload beside the folder's path, and the
    folder's own scope. The sheet reached a folder only through a finding raised about it. This
    function reaches any folder the current run holds, so a row in the Coverage view can carry
    it. It refuses an entry that is not a folder of the current run.
    """
    from ingence.core.ids import Scope as _Scope
    from ingence.core.ids import ScopeKind
    from ingence.record.targets import folder_target

    said = {"described": (described or "").strip() or None, "note": (note or "").strip() or None}
    if not any(said.values()):
        message = "an annotation says something: a description, a note, or both"
        raise AnswerRefusedError(message)
    found = catalog.query(
        "SELECT relative_path FROM entries WHERE entry_id = ? AND entry_type = 'directory' "
        "AND NOT is_deleted",
        [entry_id],
    ).fetchone()
    if found is None:
        message = f"{entry_id!r} is not a folder of the current run"
        raise AnswerRefusedError(message)
    path = str(found[0])
    return append_event(
        project,
        kind=RecordEventKind.ANNOTATE,
        actor=actor,
        occurred_at=occurred_at,
        target=folder_target(entry_id),
        against_run_id=_current_run(catalog),
        payload={
            **{name: value for name, value in said.items() if value},
            "path": path,
            "source": source if isinstance(source, str) else dict(source),
        },
        scope=_Scope(ScopeKind.FOLDER, path),
    )


#: The longest name a person gives a line of work, in characters.
THEME_LABEL_LIMIT = 200


def annotate_theme(
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    target: str,
    described: str | None,
    note: str | None,
    engine_label: str | None,
    against_run_id: str,
    source: Mapping[str, Any],
    experiments: Sequence[str] = (),
) -> RecordEvent:
    """A person's name for a line of work, or its removal (the Years view's Rename).

    The answer is one ``annotate`` event on ``theme:<key>``: the name in ``described`` (none
    removes the name from view), a note beside it, and the engine's own label as it read when the
    name was given, so the Record says what was renamed. Themes are a projection, so nothing that
    infers reads this. The Years view shows the person's name over the engine's words.

    ``experiments`` are the line's experiments when it was named. The label a line has is the
    engine's reading of its words and changes in a re-run, so the name follows the line by these
    experiments (``themes.lines.follow_names``).
    """
    name = (described or "").strip() or None
    kept_note = (note or "").strip() or None
    if name is not None and len(name) > THEME_LABEL_LIMIT:
        message = (
            f"a line's name is at most {THEME_LABEL_LIMIT} characters; this one is {len(name)}"
        )
        raise AnswerRefusedError(message)
    if name is None and kept_note is None:
        message = "naming a line of work says a name, a note, or both; none removes the name"
        raise AnswerRefusedError(message)
    return append_event(
        project,
        kind=RecordEventKind.ANNOTATE,
        actor=actor,
        occurred_at=occurred_at,
        target=target,
        against_run_id=against_run_id,
        payload={
            **({"described": name} if name else {}),
            **({"note": kept_note} if kept_note else {}),
            **({"engine_label": engine_label} if engine_label else {}),
            **({"experiments": sorted({str(e) for e in experiments})} if experiments else {}),
            "source": dict(source),
        },
    )


def line_experiments(project: Path, run_id: str, key: str) -> list[str]:
    """The experiments of the line of work called ``key`` in a run, for a name to follow it by.

    The function reads them from the run's themes projection through the theme registry, on a
    best-effort basis. Where the themes cannot be read (a component not available, a run gone),
    the name is still saved and follows its line by the key alone.
    """
    try:
        from ingence.themes import themes_engine
        from ingence.themes.lines import experiments_of_key

        return experiments_of_key(themes_engine().projection(project, run_id), key)
    except Exception:  # an optional projection: naming a line never fails on it
        return []


def say_folder_is_program(
    catalog: Catalog,
    project: Path,
    *,
    actor: str,
    occurred_at: str,
    entry_id: str,
    is_program: bool,
    note: str | None = None,
) -> RecordEvent:
    """Write a person's answer on whether one folder is an installed program or a package.

    "This is not a program" is the Programs page's per-folder answer. It is a rejection on the
    folder's program target. Inference and the dive both read it from the Record and leave the
    folder in the data's scope (:func:`~ingence.record.verdicts.folders_not_programs`).
    ``is_program=True`` is the opposite answer and puts a folder back. The function refuses an
    entry that is not a folder of the current run.
    """
    from ingence.core.ids import Scope as _Scope
    from ingence.core.ids import ScopeKind
    from ingence.record.targets import program_target
    from ingence.record.verdicts import NOT_A_PROGRAM

    found = catalog.query(
        "SELECT relative_path FROM entries WHERE entry_id = ? AND entry_type = 'directory' "
        "AND NOT is_deleted",
        [entry_id],
    ).fetchone()
    if found is None:
        message = f"{entry_id!r} is not a folder of the current run"
        raise AnswerRefusedError(message)
    path = str(found[0])
    said = (note or "").strip()
    return append_event(
        project,
        kind=RecordEventKind.CONFIRM if is_program else RecordEventKind.REJECT,
        actor=actor,
        occurred_at=occurred_at,
        target=program_target(entry_id),
        against_run_id=_current_run(catalog),
        payload={
            "word": "program" if is_program else NOT_A_PROGRAM,
            "path": path,
            **({"note": said} if said else {}),
            "source": "programs page",
        },
        scope=_Scope(ScopeKind.FOLDER, path),
    )


# ---------------------------------------------------------------------------
# Every sheet kind as a page that writes.
# ---------------------------------------------------------------------------


def _said(value: Any) -> str | None:
    """A person's words as written, without the whitespace around them, or None for none."""
    if value is None:
        return None
    spelled = str(value).strip()
    return spelled or None


def _count(value: Any) -> int | None:
    """A positive whole number a page sent, or None; anything else is refused."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        message = f"a count of rows answered together is a positive whole number, not {value!r}"
        raise AnswerRefusedError(message)
    return value


@dataclass(frozen=True, slots=True)
class Decision:
    """One row's decision as a page sends it: the row's target and what the person said.

    ``word`` is the verdict word the row is offered. For a question about what a word means, it
    is a sense the question offers (``sense`` or ``sense [axis]``), ``cannot_say``, or
    :data:`NOT_A_WORD`, and ``answer`` with ``theme_kind`` states a meaning of the person's own.
    ``reading`` says, beside :data:`NOT_A_WORD`, what the string is to a computer
    (:data:`COMPUTER_READINGS`).
    """

    target: str
    word: str | None = None
    answer: str | None = None
    theme_kind: str | None = None
    note: str | None = None
    correct_value: str | None = None
    described: str | None = None
    belongs_to: str | None = None
    reading: str | None = None
    #: Where a meaning applies, for a question about a word: everywhere (the default, and a
    #: word's meaning is usually the laboratory's), or only where the word stands like this, a
    #: scoped definition.
    where: str | None = None
    #: A person correcting what the Dictionary already settles a word as: their own meaning, for
    #: the whole project or ``below`` one folder (:func:`correct_word`).
    correcting: bool = False
    below: str | None = None
    #: A person accepting a shipped pack from the Words section. With the word ``accept`` on
    #: ``pack:<name>`` it writes the answer to "load the X pack" whatever ``domain_packs`` says,
    #: so the pack is accepted and its meanings stand above the core's. Without it, ``accept``
    #: answers the question the project asks: "load" where it names its packs, "keep" where it
    #: loads every pack, which accepts nothing.
    accept_pack: bool = False
    #: The name of a review sheet that the person imported on a page and is taking this answer
    #: from. It is recorded in the event's source as where the answer came from. It decides
    #: nothing and is never read back.
    sheet: str | None = None
    #: A term that a person recognized in their note: ``{"text", "term", "theme_kind"}``, the
    #: chip they clicked, kept as an observation about the row's own target
    #: (``record/verdicts.write_observation``). Nothing else is said with it.
    observed: Mapping[str, Any] | None = None
    #: One answer over a listed set: how many rows the person answered at once ("Correct for
    #: all 240"). Each row's event says so in its source, beside the value.
    together: int | None = None

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> Decision:
        """Read one decision from a request, refusing one that names no row."""
        target = _said(body.get("target"))
        if target is None:
            message = "a decision names the row it is about by its target"
            raise AnswerRefusedError(message)
        return cls(
            target=target,
            word=_said(body.get("word")),
            answer=_said(body.get("answer")),
            theme_kind=_said(body.get("theme_kind")),
            note=_said(body.get("note")),
            correct_value=_said(body.get("correct_value")),
            described=_said(body.get("described")),
            belongs_to=_said(body.get("belongs_to")),
            reading=_said(body.get("reading")),
            where=_said(body.get("where")),
            correcting=bool(body.get("correcting")),
            below=_said(body.get("below")),
            accept_pack=bool(body.get("accept_pack")),
            sheet=_said(body.get("sheet")),
            observed=body.get("observe") if isinstance(body.get("observe"), dict) else None,
            together=_count(body.get("together")),
        )


#: A sense as the term sheet spells one: ``standard_deviation [statistic]``.
_SENSE_SPELLING = re.compile(r"^(?P<term>.*?)\s*\[(?P<axis>[^\]]+)\]\s*$")


def _spoken(word: str | None) -> tuple[str | None, str | None]:
    """Read a sense as the term sheet spells one, ``sense [axis]``, into the sense and its axis."""
    if not word:
        return None, None
    spelled = _SENSE_SPELLING.match(word)
    if spelled:
        return spelled.group("term").strip() or None, spelled.group("axis").strip() or None
    return word, None


def glued_spellings(catalog: Catalog) -> dict[str, str]:
    """Every spelling a capital divided in the catalog's run, casefolded, to the way it is written.

    Read from the name shapes' slots (``NameSlot.case_whole`` is on each piece of such a spelling
    and on nothing else); the written form is the first in character order. Empty where the run
    recorded no name shapes.
    """
    try:
        rows = catalog.query(
            """
            SELECT DISTINCT unnest(
                from_json(json_extract(slots_json, '$[*].case_whole'), '["VARCHAR"]')
            ) AS written
            FROM name_shapes WHERE source_scope = 'name'
            """
        ).fetchall()
    except Exception:  # a run whose name shapes were not recorded
        return {}
    found: dict[str, str] = {}
    for (written,) in sorted(rows, key=lambda row: str(row[0])):
        if written:
            found.setdefault(str(written).casefold(), str(written))
    return found


@dataclass
class DecisionContext:
    """What deciding rows against the current run reads, read once however many rows are written.

    Holds the catalog and project it decides against, and reads the queue, the classes, the
    rules' predicates and the extensions only when a row needs them. A group of fifty rows reads
    each once.
    """

    catalog: Catalog
    project: Path
    _questions: list[dict[str, Any]] | None = field(default=None, repr=False)
    _classes: dict[str, Subject] | None = field(default=None, repr=False)
    _predicates: dict[str, str] | None = field(default=None, repr=False)
    _extensions: dict[str, tuple[int, int]] | None = field(default=None, repr=False)
    _run: str | None = field(default=None, repr=False)
    _answered: frozenset[str] | None = field(default=None, repr=False)
    _store: DictionaryStore | None = field(default=None, repr=False)
    _glued: dict[str, str] | None = field(default=None, repr=False)
    #: The terms this context's own writes named, with the kind each was given. A batch that
    #: defines a head and assigns its variants to it must know, for the second, what the first
    #: wrote, though the Dictionary was loaded once before either.
    defined: dict[str, str] = field(default_factory=dict, repr=False)

    def store(self) -> DictionaryStore:
        """The project's Dictionary, loaded once however many rows are decided.

        ``for_project`` parses every shipped file again on each call, so a batch of twenty words
        would load it twenty times. The property reads it at the first row that needs it, so it
        holds what the Record held then. A row written meanwhile is known through
        :attr:`defined`.
        """
        if self._store is None:
            from ingence.dictionary.store import DictionaryStore

            self._store = DictionaryStore.for_project(self.project)
        return self._store

    def glued(self) -> dict[str, str]:
        """Every spelling a capital divided in the current run, casefolded, to its written form."""
        if self._glued is None:
            self._glued = glued_spellings(self.catalog)
        return self._glued

    def against_run(self) -> str:
        if self._run is None:
            self._run = _current_run(self.catalog)
        return self._run

    def questions(self) -> list[dict[str, Any]]:
        if self._questions is None:
            self._questions = current_questions(self.catalog)
        return self._questions

    def predicate(self, rule_id: str) -> str | None:
        if self._predicates is None:
            self._predicates = {
                str(rule): str(predicate or "")
                for rule, predicate in self.catalog.query(
                    "SELECT rule_id, predicate FROM rules"
                ).fetchall()
            }
        return self._predicates.get(rule_id)

    def class_subject(self, target: str) -> Subject | None:
        if self._classes is None:
            index = class_index(self.catalog)
            self._classes = {}
            for key, rules in index.by_key.items():
                for rule in rules:
                    named = class_target(rule, key)
                    self._classes[named] = Subject(
                        target=named,
                        rule_id=rule,
                        rule_version=index.versions.get(rule, ""),
                        about={"key": key},
                    )
        found = self._classes.get(target)
        if found is None and target not in self._classes:
            # A class of claims is answered on its handle: every claim that one rule made with
            # one value, the class named by the rule and the value as a grouping class is by its
            # key. What the page showed travels with the answer: its size, its three examples
            # and its members, by the one membership that the handle and inference read, and
            # whether the rule's reading can be retracted at all.
            from ingence.api.plain_words import rule_sentence
            from ingence.review.claim_classes import class_named_by

            held = class_named_by(self.catalog, target)
            if held is not None:
                found = Subject(
                    target=target,
                    rule_id=held.rule_id,
                    rule_version=held.rule_version,
                    about={
                        "of": "claims",
                        "value": held.value,
                        "size": held.size,
                        "examples": list(held.examples),
                        "members": sorted({subject for _, subject in held.members}),
                        "retractable": held.retractable,
                        # The rule's sentence, for what waits for Re-run to say in plain words.
                        "rule_said": rule_sentence(held.rule_id) or "",
                    },
                )
                self._classes[target] = found
        return found

    def in_force(self, target: str) -> bool:
        """Whether a decision is in force on a target, the Record read once per context."""
        if self._answered is None:
            self._answered = frozenset(latest_by_target(read_events(self.project)))
        return target in self._answered

    def extensions(self) -> dict[str, tuple[int, int]]:
        if self._extensions is None:
            self._extensions = extension_counts(self.catalog)
        return self._extensions


def _computer_reading(reading: str) -> str:
    """One of :data:`COMPUTER_READINGS`, or a refusal naming them."""
    spelled = reading.strip().lower()
    if spelled not in COMPUTER_READINGS:
        allowed = ", ".join(COMPUTER_READINGS)
        message = f"{reading!r} is not something a string can be to a computer here: {allowed}"
        raise AnswerRefusedError(message)
    return spelled


def _moved(what: str) -> QuestionMovedError:
    return QuestionMovedError(
        f"the current run holds no {what}: the question is no longer asked (a later run "
        "answered it, or no longer raises it)."
    )


def _offered_word(word: str, offered: Sequence[str], what: str) -> str:
    if word not in offered:
        message = f"{word!r} is not something {what} can be answered with: {', '.join(offered)}"
        raise AnswerRefusedError(message)
    return word


def _verdict_or_note(
    context: DecisionContext,
    subject: Subject,
    decision: Decision,
    offered: Sequence[str],
    what: str,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """Write a verdict word about one subject, or the person's note alone.

    ``write_verdict`` and ``write_note`` are what the sheet import writes through, so the event
    is the sheet's, but for the source naming this page.
    """
    try:
        if decision.word:
            word = _offered_word(decision.word, offered, what)
            return write_verdict(
                context.project,
                subject,
                word,
                actor=actor,
                occurred_at=occurred_at,
                against_run_id=context.against_run(),
                note=decision.note,
                correct_stage=decision.correct_value,
                source=source,
            )
        if decision.correct_value:
            message = "a right value is named only beside 'wrong'; say wrong as well"
            raise AnswerRefusedError(message)
        if decision.note:
            return write_note(
                context.project,
                subject,
                decision.note,
                actor=actor,
                occurred_at=occurred_at,
                against_run_id=context.against_run(),
                source=source,
            )
    except VerdictRefusedError as error:
        raise AnswerRefusedError(str(error)) from error
    message = "a decision says something: a word, a note, or both"
    raise AnswerRefusedError(message)


#: The words a findings sheet answers a word question with, as a verdict on the question.
_VERDICT_ON_A_WORD_QUESTION = frozenset({"correct", "wrong"})


def _scope_of_answer(
    catalog: Catalog, rule_id: str, raised: str, where: str | None, *, names_a_meaning: bool
) -> Scope:
    """The scope a question's meaning applies at: the project, or only where the word stands.

    The default is everywhere, because what a word means is usually the laboratory's and not one
    folder's. "Only where it stands" is a scoped definition: the deepest folder that holds every
    folder the run asked about the word in, outside installed programs, and what lies beneath it,
    and never further. The page and this function both use
    :func:`~ingence.textword.questions.scope_where_it_stands`, for a word's findings on folders
    and on files alike. So the page never offers a folder that this function refuses. This
    function refuses, with that function's plain reason, wherever a folder scope would reach past
    those places, and beside anything but a meaning, which is the only thing a scope changes.
    """
    from ingence.textword.questions import EVERYWHERE, WHERE_IT_STANDS, scope_where_it_stands

    if where in (None, EVERYWHERE):
        return PROJECT_SCOPE
    if where != WHERE_IT_STANDS:
        message = (
            f"A meaning applies everywhere, or only where the word stands; {where!r} is neither."
        )
        raise AnswerRefusedError(message)
    if not names_a_meaning:
        message = (
            "Only a meaning can be kept to where the word stands: 'not a word', 'cannot say' and "
            "a note are about the question wherever it is asked."
        )
        raise AnswerRefusedError(message)
    stands = scope_where_it_stands(catalog, rule_id, raised)
    if stands.folder is None:
        message = f"A meaning for {raised!r} cannot be kept to where it stands. {stands.because}"
        raise AnswerRefusedError(message)
    return Scope(ScopeKind.FOLDER, stands.folder)


def _about_a_word(context: DecisionContext, rule_id: str, question: Mapping[str, Any]) -> bool:
    """Whether a question asks about a word: one offering senses, or one the word rule raised."""
    from ingence.textword.questions import PREDICATE

    return bool(question["senses"]) or context.predicate(rule_id) == PREDICATE


def _decide_question_on_its_proposal(
    context: DecisionContext,
    target: str,
    rule_id: str,
    raised: str,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """Write an answer to a proposal on the proposal's own target."""
    offered = question_takes(context.predicate(rule_id))
    word = decision.word
    if word is not None and word not in offered:
        message = f"this question takes {', '.join(offered)}; {word!r} is not one of them"
        raise AnswerRefusedError(message)
    if word is None and not decision.note:
        message = "an answer says a word, or leaves a note"
        raise AnswerRefusedError(message)
    kind = VERDICTS[word] if word is not None else RecordEventKind.ANNOTATE
    said = (decision.note or "").strip()
    return append_event(
        context.project,
        kind=kind,
        actor=actor,
        occurred_at=occurred_at,
        target=target,
        against_run_id=_current_run(context.catalog),
        payload={
            **({"verdict": word} if word else {}),
            **({"note": said} if said else {}),
            "question": question_target(rule_id, raised),
            "source": dict(source),
        },
    )


def _decide_question(
    context: DecisionContext,
    rest: str,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """A question: a word's meaning where it offers senses, otherwise a verdict word.

    A question offering no sense may still be given a meaning of the person's own (``answer``
    with ``theme_kind``), which is how a region question's term is defined.
    """
    rule_id, _, raised = rest.partition(":")
    question = _question(context.catalog, rule_id, raised, context.questions())
    # A proposal is answered on its own target: a group's core, a folder's miscellany or
    # program answer, or a pack (``record/proposals.py``), read back by the engine.
    from ingence.record.proposals import proposal_target

    target = proposal_target(
        context.catalog,
        context.predicate(rule_id),
        rule_id,
        raised,
        str(question.get("reason") or ""),
    )
    if target is not None:
        return _decide_question_on_its_proposal(
            context,
            target,
            rule_id,
            raised,
            decision,
            actor=actor,
            occurred_at=occurred_at,
            source=source,
        )
    if decision.word == NOT_A_WORD and not _about_a_word(context, rule_id, question):
        # "Not a word" answers a question about a word, and no other. Without this check, the
        # answer would write a rejection on any question.
        takes = ", ".join(
            word.replace("_", " ") for word in question_words(context.predicate(rule_id))
        )
        message = (
            f"'not a word' answers a question about what a word is, and this question is about "
            f"something else. It takes: {takes}."
        )
        raise AnswerRefusedError(message)
    # The findings sheet's verdict on a word question: "correct" says the word does stand
    # between these senses, "wrong" that it does not, and neither says what it means. The sheet
    # wrote it as a verdict on the question, so the page writes the same event.
    as_a_verdict = decision.word in _VERDICT_ON_A_WORD_QUESTION and not decision.answer
    # A word question offers no sense and still takes one: the question about a word that
    # nothing binds is answered by saying what it is, ``sense [axis]`` as the term sheet spells
    # it, or that it is not a word.
    says_a_meaning = bool(_spoken(decision.word)[1]) or decision.word == NOT_A_WORD
    if (not question["senses"] or as_a_verdict) and not decision.answer and not says_a_meaning:
        _scope_of_answer(context.catalog, rule_id, raised, decision.where, names_a_meaning=False)
        subject = question_subject(context.catalog, rule_id, raised)
        if subject is None:
            raise _moved(f"question {raised!r} from {rule_id!r}")
        return _verdict_or_note(
            context,
            subject,
            decision,
            question_words(context.predicate(rule_id)),
            "this question",
            actor=actor,
            occurred_at=occurred_at,
            source=source,
        )
    # A question about what a word means: the term sheet's path, which is the Review view's own.
    common: dict[str, Any] = {
        "actor": actor,
        "occurred_at": occurred_at,
        "rule_id": rule_id,
        "raised": raised,
        "note": decision.note,
        "questions": context.questions(),
        "source": source,
    }
    offered = {str(sense["term"]) for sense in question["senses"]}
    sense, axis = _spoken(decision.word)
    if decision.answer:
        sense, axis = decision.answer, decision.theme_kind or axis
    scope = _scope_of_answer(
        context.catalog,
        rule_id,
        raised,
        decision.where,
        names_a_meaning=bool(sense) and decision.word not in ("cannot_say", NOT_A_WORD),
    )
    if decision.word == "cannot_say" and not decision.answer:
        return answer_question(
            context.catalog, context.project, kind=RecordEventKind.ANNOTATE, **common
        )
    if decision.word == NOT_A_WORD and not decision.answer:
        return answer_question(
            context.catalog,
            context.project,
            kind=RecordEventKind.REJECT,
            reading=_computer_reading(decision.reading) if decision.reading else None,
            **common,
        )
    if decision.reading:
        message = "what a string is to a computer is said only beside 'not a word'"
        raise AnswerRefusedError(message)
    if sense:
        kind = RecordEventKind.ASSIGN if sense in offered else RecordEventKind.DEFINE
        return answer_question(
            context.catalog,
            context.project,
            kind=kind,
            answer=sense,
            theme_kind=axis,
            scope=scope,
            **common,
        )
    if decision.note:
        # The term sheet's note: about the question, naming what was raised and nothing else.
        subject = Subject(
            target=question_target(rule_id, raised),
            rule_id=rule_id,
            rule_version=str(question.get("rule_version") or ""),
            about={"raised": raised},
        )
        return write_note(
            context.project,
            subject,
            decision.note,
            actor=actor,
            occurred_at=occurred_at,
            against_run_id=context.against_run(),
            source=source,
        )
    message = "a decision says something: a meaning, a word, a note, or both"
    raise AnswerRefusedError(message)


def _decide_extension(
    context: DecisionContext,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """An extension: whether it is worth reading, what it is, what it belongs to, and a note."""
    extension = extension_named_by(decision.target)
    counts = context.extensions()
    if extension is None or extension not in counts:
        raise _moved(f"file with the extension {decision.target.partition(':')[2]!r}")
    worth = (decision.word or "").lower() or None
    if worth is not None and worth not in WORTH_READING:
        allowed = ", ".join(WORTH_READING)
        message = f"{worth!r} is not a worth-reading answer: {allowed}"
        raise AnswerRefusedError(message)
    said: dict[str, str | None] = {
        "worth_reading": worth,
        "described": decision.described,
        "belongs_to": decision.belongs_to,
        "note": decision.note,
    }
    if not any(said.values()):
        message = "a decision about an extension says something: a word, its words, or a note"
        raise AnswerRefusedError(message)
    files, size = counts[extension]
    kind, payload = extension_event(extension, said, files=files, size=size, source=source)
    return append_event(
        context.project,
        kind=kind,
        actor=actor,
        occurred_at=occurred_at,
        target=extension_target(extension),
        against_run_id=context.against_run(),
        payload=payload,
        scope=PROJECT_SCOPE,
    )


def _decide_word(
    context: DecisionContext,
    word: str,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """A word no rule asked about: a meaning (define_word), not a word at all, or a note."""
    sense, axis = _spoken(decision.word)
    if decision.answer:
        sense, axis = decision.answer, decision.theme_kind or axis
    if decision.correcting:
        if not sense or sense in (NOT_A_WORD, "cannot_say"):
            message = "a correction gives the word a meaning and says what kind of thing it is"
            raise AnswerRefusedError(message)
        corrected = correct_word(
            context.catalog,
            context.project,
            actor=actor,
            occurred_at=occurred_at,
            word=word,
            answer=sense,
            theme_kind=axis or "",
            below=decision.below,
            note=decision.note,
            source=source,
            store=context.store(),
            known_terms=context.defined,
        )
        context.defined[sense] = (axis or "").strip()
        return corrected
    if decision.below and not (sense and sense not in (NOT_A_WORD, "cannot_say")):
        message = "a folder is named only for a meaning: say what the word means below it"
        raise AnswerRefusedError(message)
    if sense and sense not in (NOT_A_WORD, "cannot_say"):
        defined = define_word(
            context.catalog,
            context.project,
            actor=actor,
            occurred_at=occurred_at,
            word=word,
            answer=sense,
            theme_kind=axis or "",
            below=decision.below,
            note=decision.note,
            source=source,
            store=context.store(),
            known_terms=context.defined,
        )
        context.defined[sense.strip()] = (axis or "").strip()
        return defined
    held = context.catalog.query(
        "SELECT count(DISTINCT entry_id) FROM components WHERE lower(normalized_value) = ?",
        [word.casefold()],
    ).fetchone()
    files = int(held[0]) if held else 0
    if files == 0:
        raise _moved(f"name carrying the word {word!r}")
    if decision.reading and sense != NOT_A_WORD:
        message = "what a string is to a computer is said only beside 'not a word'"
        raise AnswerRefusedError(message)
    if sense == NOT_A_WORD:
        kind = RecordEventKind.REJECT
    elif decision.note:
        kind = RecordEventKind.NOTE
    else:
        message = "a word takes a meaning, 'not a word', or a note"
        raise AnswerRefusedError(message)
    return append_event(
        context.project,
        kind=kind,
        actor=actor,
        occurred_at=occurred_at,
        target=term_target(word),
        against_run_id=context.against_run(),
        payload={
            "raised": word.casefold(),
            "as_shown": {"files": files},
            "source": dict(source),
            **({"verdict": NOT_A_WORD} if kind is RecordEventKind.REJECT else {}),
            **(
                {"reading": _computer_reading(decision.reading)}
                if kind is RecordEventKind.REJECT and decision.reading
                else {}
            ),
            **({"note": decision.note} if decision.note else {}),
        },
        scope=PROJECT_SCOPE,
    )


#: What a person says of a spelling a capital divides: the words the Words page offers, and the
#: reading each is (``dictionary/case_reading.py`` reads ``reading`` and, failing it, ``answer``).
_SPLIT_WORDS: dict[str, tuple[str, str]] = {
    "whole": ("whole", "one word"),
    "one word": ("whole", "one word"),
    "pieces": ("pieces", "separate words"),
    "separate words": ("pieces", "separate words"),
}

#: The one word that says two spellings are unrelated.
UNRELATED = "unrelated"


def _decide_split(
    context: DecisionContext,
    spelling: str,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """How a spelling that a capital divides reads: one word, or separate words.

    This is the writer for what the census already has a reader for
    (``dictionary/case_reading.py``). The target is ``split:<spelling>`` casefolded, and the
    payload's ``reading`` is ``whole`` or ``pieces``, which the binder takes ahead of the counts
    and the Dictionary. The function refuses a spelling that the current run did not divide (a
    page out of date), and a word that is neither reading.
    """
    said = _SPLIT_WORDS.get((decision.word or "").strip().casefold())
    if said is None:
        message = "a divided spelling reads as one word (whole) or as separate words (pieces)"
        raise AnswerRefusedError(message)
    folded = spelling.strip().casefold()
    written = context.glued().get(folded)
    if written is None:
        raise _moved(f"divided spelling {spelling!r}")
    reading, answer = said
    return append_event(
        context.project,
        kind=RecordEventKind.ASSIGN,
        actor=actor,
        occurred_at=occurred_at,
        target=f"{SPLIT_PREFIX}:{folded}",
        against_run_id=context.against_run(),
        payload={
            "reading": reading,
            "answer": answer,
            "written": written,
            "source": dict(source),
            **({"note": decision.note} if decision.note else {}),
        },
        scope=PROJECT_SCOPE,
    )


def _decide_distinct(
    context: DecisionContext,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """Two spellings a person says are unrelated words: a ``reject`` that keeps them apart.

    A ``reject`` reads as "not these together", and a later ``confirm`` on the same target takes
    it back. The Words page stops offering the pair (``dictionary/variants.py`` reads it). No run
    reads it, so it waits for nothing. The function refuses a target that names no pair, a word
    that is not :data:`UNRELATED`, and a current run that carries neither spelling.
    """
    pair = distinct_named_by(decision.target)
    if pair is None:
        # One pair, one target: say how the pair is written when it is only written another way
        # (capitals, spaces, the other order), so a page that sends it knows what to send.
        spellings = decision.target.partition(":")[2].split("+")
        try:
            written = distinct_target(*spellings)
        except (TypeError, ValueError):
            written = ""
        message = (
            f"write that pair as {written}: lowercase, the two spellings sorted"
            if written
            else "a pair is named as distinct:<spelling>+<spelling>, two different spellings"
        )
        raise AnswerRefusedError(message)
    if (decision.word or "").strip().casefold() != UNRELATED:
        message = f"a pair is answered {UNRELATED!r}, and {decision.word!r} was given"
        raise AnswerRefusedError(message)
    carried = context.catalog.query(
        "SELECT count(*) FROM components WHERE component_type = 'word' "
        "AND lower(normalized_value) IN (?, ?)",
        [pair[0], pair[1]],
    ).fetchone()
    if not carried or not int(carried[0]):
        raise _moved(f"name carrying {pair[0]!r} or {pair[1]!r}")
    return append_event(
        context.project,
        kind=RecordEventKind.REJECT,
        actor=actor,
        occurred_at=occurred_at,
        target=decision.target,
        against_run_id=context.against_run(),
        payload={
            "verdict": UNRELATED,
            "spellings": list(pair),
            "source": dict(source),
            **({"note": decision.note} if decision.note else {}),
        },
        scope=PROJECT_SCOPE,
    )


def _decide_pack(
    context: DecisionContext,
    decision: Decision,
    domain: str,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """Answer a domain on ``pack:<domain>``: accept is a prior, and refuse is a veto.

    Every domain's words load either way. A confirm makes the domain a prior, which reads a word
    shared between domains as this domain's where a folder's files do not decide. A reject leaves
    the domain's words out of the Dictionary. The payload still names the question the answer is
    given to, so an older engine reads it.
    """
    from ingence.api.routes.proposals import PROPOSAL_WORDS
    from ingence.dictionary.packs.proposals import PACK_RULE
    from ingence.record.questions import question_target
    from ingence.rulebook.predicates.packs import pack_value

    offered = {item["word"]: item["kind"] for item in PROPOSAL_WORDS[PACK_PREFIX]}
    if decision.word is not None and decision.word not in offered:
        message = f"{decision.word!r} is not an answer here; the answers are {', '.join(offered)}"
        raise AnswerRefusedError(message)
    if decision.word is None and not decision.note:
        message = "a pack takes accept or refuse, or a note"
        raise AnswerRefusedError(message)
    if decision.accept_pack and decision.word != "accept":
        # Said as an acceptance, any other word is a page's mistake: a refuse here would be a
        # veto that leaves the domain's words out of the Dictionary.
        message = "accept_pack accepts the pack: say the word accept with it"
        raise AnswerRefusedError(message)
    kind = (
        RecordEventKind(offered[decision.word])
        if decision.word is not None
        else RecordEventKind.NOTE
    )
    asked = pack_value(domain)
    return append_event(
        context.project,
        kind=kind,
        actor=actor,
        occurred_at=occurred_at,
        target=decision.target,
        against_run_id=context.against_run(),
        payload={
            "source": dict(source),
            **({"verdict": decision.word} if decision.word is not None else {}),
            **({"note": decision.note} if decision.note else {}),
            "question": question_target(PACK_RULE, asked),
        },
    )


def _decide_proposal(
    context: DecisionContext,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """Accept or refuse a proposal: a vocabulary pack, miscellaneous files, or a group's words.

    The answer is written on the proposal's own target, ``pack:<domain>``,
    ``miscellaneous:<entry_id>`` or ``group:<terms>``, as a confirm (``accept``) or a reject
    (``refuse``), which the next run reads. A note alone is kept beside it and decides nothing.
    The function refuses a pack that does not ship, a folder the run lacks, and a group's words
    that are not written as its target spells them.
    """
    from ingence.api.routes.proposals import PROPOSAL_WORDS, target_is_proposed

    why_not = target_is_proposed(context.catalog, context.project, decision.target)
    if why_not is not None:
        raise AnswerRefusedError(why_not)
    if decision.word == CANNOT_SAY:
        # "Can't say" on a proposal is an annotation. A proposal is taken or refused, so a person
        # who cannot tell leaves a note, which decides nothing and keeps the question open. It is
        # the note that the sheet has always written for them, said by the word.
        said = "Can't say" + (f". {decision.note}" if decision.note else "")
        decision = replace(decision, word=None, note=said)
    prefix, _, rest = decision.target.partition(":")
    if prefix == PACK_PREFIX:
        return _decide_pack(
            context, decision, rest, actor=actor, occurred_at=occurred_at, source=source
        )
    offered = {item["word"]: item["kind"] for item in PROPOSAL_WORDS[prefix]}
    if decision.word is not None and decision.word not in offered:
        message = f"{decision.word!r} is not an answer here; the answers are {', '.join(offered)}"
        raise AnswerRefusedError(message)
    if decision.word is None and not decision.note:
        message = "a proposal takes accept or refuse, or a note"
        raise AnswerRefusedError(message)
    kind = (
        RecordEventKind(offered[decision.word])
        if decision.word is not None
        else RecordEventKind.NOTE
    )
    return append_event(
        context.project,
        kind=kind,
        actor=actor,
        occurred_at=occurred_at,
        target=decision.target,
        against_run_id=context.against_run(),
        payload={
            "source": dict(source),
            **({"verdict": decision.word} if decision.word is not None else {}),
            **({"note": decision.note} if decision.note else {}),
        },
        scope=PROJECT_SCOPE,
    )


def _decide_analysed_elsewhere(
    context: DecisionContext,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """Say an experiment's analysis is elsewhere, or take that back.

    The answer is a confirm or a reject on the experiment's own target
    (``analysed_elsewhere:<entry_id>``, ``completeness/elsewhere.py``). ``elsewhere`` says the
    analysis of this experiment is somewhere the engine did not read, and ``not_elsewhere`` takes
    that back. The next run reads the answer, and the current run does not. A note alone is kept
    beside it and decides nothing. The function refuses a folder that is not an experiment the
    current run found, because no page could have shown it.
    """
    from ingence.completeness.elsewhere import ELSEWHERE_WORDS, experiment_folder_of
    from ingence.core.ids import Scope as _Scope
    from ingence.core.ids import ScopeKind
    from ingence.record.targets import experiment_analysed_elsewhere

    entry_id = experiment_analysed_elsewhere(decision.target)
    path = experiment_folder_of(context.catalog, entry_id) if entry_id is not None else None
    if path is None:
        message = (
            f"the current run found no experiment {entry_id or decision.target!r}: an answer that "
            "an analysis is elsewhere is about an experiment the run found"
        )
        raise AnswerRefusedError(message)
    offered = {item["word"]: item["kind"] for item in ELSEWHERE_WORDS}
    if decision.word is not None and decision.word not in offered:
        message = f"{decision.word!r} is not an answer here; the answers are {', '.join(offered)}"
        raise AnswerRefusedError(message)
    if decision.word is None and not decision.note:
        message = "an experiment's analysis takes elsewhere or not_elsewhere, or a note"
        raise AnswerRefusedError(message)
    kind = (
        RecordEventKind(offered[decision.word])
        if decision.word is not None
        else RecordEventKind.NOTE
    )
    return append_event(
        context.project,
        kind=kind,
        actor=actor,
        occurred_at=occurred_at,
        target=decision.target,
        against_run_id=context.against_run(),
        payload={
            "source": dict(source),
            "path": path,
            **({"verdict": decision.word} if decision.word is not None else {}),
            **({"note": decision.note} if decision.note else {}),
        },
        scope=_Scope(ScopeKind.FOLDER, path),
    )


def _observed_subject(context: DecisionContext, target: str) -> Subject:
    """The subject an observation is kept about: a row's own target, found in the current run."""
    prefix, _, rest = target.partition(":")
    found: Subject | None = None
    if prefix == QUESTION_PREFIX and rest:
        rule_id, _, raised = rest.partition(":")
        found = question_subject(context.catalog, rule_id, raised)
    elif prefix == CLASS_PREFIX and rest:
        found = context.class_subject(target)
    elif prefix == CLAIM_PREFIX and rest:
        found = claim_subjects(context.catalog, [rest]).get(rest)
    elif prefix == CANDIDATE_PREFIX and rest:
        found = candidate_subjects(context.catalog, [rest]).get(rest)
    elif prefix == EXTENSION_PREFIX and rest:
        extension = extension_named_by(target)
        if extension is not None and extension in context.extensions():
            found = Subject(target=target, rule_id="", rule_version="", about={})
    else:
        message = (
            "an observation is kept about a question, a group, a conclusion, a pair or an "
            "extension, "
            f"not {target!r}"
        )
        raise AnswerRefusedError(message)
    if found is None:
        raise _moved(f"row {target}")
    return found


def _observe(
    context: DecisionContext,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    source: Mapping[str, Any],
) -> RecordEvent:
    """Keep a term that a person recognized in their note with the row's own target."""
    if decision.word or decision.answer or decision.note:
        message = "a term from a note is kept on its own, with no word, answer or note beside it"
        raise AnswerRefusedError(message)
    subject = _observed_subject(context, decision.target)
    try:
        return write_observation(
            context.project,
            subject,
            [dict(decision.observed or {})],
            actor=actor,
            occurred_at=occurred_at,
            against_run_id=context.against_run(),
            source=source,
        )
    except VerdictRefusedError as error:
        raise AnswerRefusedError(str(error)) from error


def decide_target(
    context: DecisionContext,
    decision: Decision,
    *,
    actor: str,
    occurred_at: str,
    also: Mapping[str, Any] | None = None,
) -> RecordEvent:
    """Write one row's decision to the Record, the event its sheet would write, and return it.

    The row is named by its target, so a page never sends anything that the engine must trust to
    find the row. A target that the current run does not hold is a page out of date
    (:class:`QuestionMovedError`, a conflict). ``also`` is what an import adds to the event's
    source (``api/routes/imports.py``): the file, and who imported it.
    """
    if not actor.strip():
        message = "a decision names who made it; a decision by nobody is not attributable"
        raise AnswerRefusedError(message)
    prefix, _, rest = decision.target.partition(":")
    if decision.accept_pack and prefix != PACK_PREFIX:
        message = "accept_pack is said of a pack, on pack:<name>"
        raise AnswerRefusedError(message)
    if decision.together is not None and context.in_force(decision.target):
        # One answer over a list keeps what was answered since the list was shown.
        message = f"{decision.target} was answered since the list was shown, and keeps that answer"
        raise AlreadyAnsweredError(message)
    source: dict[str, Any] = {"page": PAGES.get(prefix, prefix)}
    if decision.sheet is not None:
        source["imported_from"] = decision.sheet
    if decision.together is not None:
        source["answered_together"] = decision.together
    if also:
        source.update(also)
    common: dict[str, Any] = {"actor": actor, "occurred_at": occurred_at, "source": source}
    if decision.observed is not None:
        return _observe(context, decision, **common)

    if prefix == QUESTION_PREFIX and rest:
        return _decide_question(context, rest, decision, **common)
    if prefix == CLASS_PREFIX and rest:
        subject = context.class_subject(decision.target)
        if subject is None:
            raise _moved(f"class {decision.target}")
        if subject.about.get("of") == "claims":
            # A class of claims takes a claim's words: its reading is right or wrong. A person
            # answers it on the Questions page (its sheet) and on an experiment's files, never on
            # Groupings, so its answer says so.
            common["source"]["page"] = CLAIM_CLASS_PAGE
            return _verdict_or_note(
                context, subject, decision, CLAIM_WORDS, "a class of conclusions", **common
            )
        # The files the person was shown travel with the answer.
        subject = class_shown(context.catalog, subject)
        # Every word the class takes: a returned sheet may still say one that was offered before.
        words = class_takes(context.predicate(subject.rule_id))
        return _verdict_or_note(context, subject, decision, words, "a class", **common)
    if prefix == CLAIM_PREFIX and rest:
        found = claim_subjects(context.catalog, [rest]).get(rest)
        if found is None:
            raise _moved(f"conclusion {rest!r}")
        return _verdict_or_note(context, found, decision, CLAIM_WORDS, "a conclusion", **common)
    if prefix == CANDIDATE_PREFIX and rest:
        pair = candidate_subjects(context.catalog, [rest]).get(rest)
        if pair is None:
            raise CandidateMovedError(
                f"the current run does not propose the pair {rest!r}: it is no longer proposed (a "
                "later run answered it, or no longer proposes it)."
            )
        return _verdict_or_note(context, pair, decision, CANDIDATE_WORDS, "a pair", **common)
    if prefix == EXTENSION_PREFIX and rest:
        return _decide_extension(context, decision, **common)
    if prefix == TERM_PREFIX and rest:
        return _decide_word(context, rest, decision, **common)
    if prefix == SPLIT_PREFIX and rest:
        return _decide_split(context, rest, decision, **common)
    if prefix == DISTINCT_PREFIX and rest:
        return _decide_distinct(context, decision, **common)
    if prefix in (PACK_PREFIX, MISCELLANEOUS_PREFIX, GROUP_PREFIX) and rest:
        return _decide_proposal(context, decision, **common)
    if prefix == ANALYSED_ELSEWHERE_PREFIX and rest:
        return _decide_analysed_elsewhere(context, decision, **common)
    if prefix == THEME_PREFIX and rest:
        # A person's name for a line of work. ``engine_label`` travels in the body as
        # ``reading`` (the engine's own words at the time), never as a decision.
        return annotate_theme(
            context.project,
            actor=actor,
            occurred_at=occurred_at,
            target=decision.target,
            described=decision.described,
            note=decision.note,
            engine_label=decision.reading,
            against_run_id=context.against_run(),
            source=source,
            experiments=line_experiments(context.project, context.against_run(), rest),
        )
    if prefix == FOLDER_PREFIX and rest:
        return annotate_folder(
            context.catalog,
            context.project,
            actor=actor,
            occurred_at=occurred_at,
            entry_id=rest,
            described=decision.described,
            note=decision.note,
            source=source,
        )
    message = f"{decision.target!r} is not a row any page decides"
    raise AnswerRefusedError(message)


def decide_many(
    context: DecisionContext,
    decisions: Sequence[Decision],
    *,
    actor: str,
    occurred_at: str,
    also: Mapping[str, Any] | None = None,
) -> tuple[list[RecordEvent], list[dict[str, str]]]:
    """Write a selected group's decisions, one event per row, and say what became of each.

    Each row is its own decision, as each row of a sheet is. The function writes one note (and a
    word, where one was chosen) to each row. It refuses every row on its own terms and writes the
    rest, as an import counts a refusal by row and does not refuse the sheet.
    """
    written: list[RecordEvent] = []
    refused: list[dict[str, str]] = []
    for decision in decisions:
        try:
            written.append(
                decide_target(context, decision, actor=actor, occurred_at=occurred_at, also=also)
            )
        except QuestionMovedError as error:
            refused.append({"target": decision.target, "error": str(error), "moved": "yes"})
        except AlreadyAnsweredError as error:
            refused.append({"target": decision.target, "error": str(error), "already": "yes"})
        except AnswerRefusedError as error:
            refused.append({"target": decision.target, "error": str(error)})
    return written, refused


def recorded_answers(project: Path, *, limit: int = 200) -> dict[str, Any]:
    """Return the Record as the Review view shows it: newest first, and nothing hidden.

    Every event, not only the last one per question, because a person changing their mind is
    itself a decision and the log exists so that it stays visible. The page marks which is
    currently in force; the earlier ones remain readable underneath it.
    """
    events = read_events(project)
    in_force = {event.event_id for event in latest_by_target(events).values()}
    newest = sorted(events, key=lambda event: event.sequence, reverse=True)[:limit]
    return {
        "total": len(events),
        "events": [
            {
                "sequence": event.sequence,
                "event_id": event.event_id,
                "kind": event.kind.value,
                "actor": event.actor,
                "occurred_at": event.occurred_at,
                "target": event.target,
                "scope": {"kind": event.scope.kind.value, "value": event.scope.value},
                "against_run_id": event.against_run_id,
                "payload": event.payload,
                "in_force": event.event_id in in_force,
            }
            for event in newest
        ],
    }
