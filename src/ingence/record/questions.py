"""What a person answered about a question, read back out of the Record.

The Record is one of the four inputs that make an interpretation reproducible. This module makes
that true of a Dictionary answer. ``record/log.py`` is the write path. This module is the half that
reads a decision back, so that something can act on it. Without it, a person could answer "here that
term means this" and the engine would conclude what it concluded before, because the Review view
wrote to the Record and nothing read it.

The target's spelling lives here and not in the view that writes it. A target assembled at two call
sites is two spellings of one subject, and the Record would then hold one person's two answers to
one question as answers to two. The view imports the spelling from here, and so does the Dictionary.
That stops the Dictionary depending on an HTTP route.

Later decisions win and every earlier one stays readable. This is
:func:`~ingence.record.log.latest_by_target`, and it is the reason the log is append-only. A person
can see that they changed their mind, and when.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.ids import PROJECT_SCOPE, Scope
from ingence.core.schema import RecordEvent, RecordEventKind
from ingence.record.log import latest_by_target, read_events

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

__all__ = [
    "TARGET_PREFIX",
    "TERM_PREFIX",
    "Orphaned",
    "QuestionAnswer",
    "answers_in_force",
    "orphaned_answers",
    "question_target",
    "term_target",
    "terms_in_force",
]

#: Prefix on a question's target, so a Record reader can tell a decision about a question
#: from one about an entry or an edge without parsing the rest.
TARGET_PREFIX = "question"

#: The two answers that add vocabulary. ``confirm``, ``reject`` and ``annotate`` are a person
#: saying something about a question without saying what a term means, so nothing is read from
#: them here and they stay in the log for a person to read.
NAMING_KINDS = frozenset({RecordEventKind.ASSIGN, RecordEventKind.DEFINE})


def question_target(rule_id: str, raised: str) -> str:
    """Return the canonical target of a decision about one question."""
    return f"{TARGET_PREFIX}:{rule_id}:{raised}"


#: The prefix on a word's target: a person saying what a word means where no rule asked. A word that
#: nobody contests raises no question, so the Review view's route, whose target is a question, has
#: nothing to write against. A word that the Coverage view lists as binding to nothing is the target
#: itself.
TERM_PREFIX = "term"


def term_target(word: str) -> str:
    """Return the canonical target of a decision about what one word means, casefolded."""
    return f"{TERM_PREFIX}:{word.strip().casefold()}"


@dataclass(frozen=True, slots=True)
class QuestionAnswer:
    """One person's in-force answer naming what a raised term means.

    The kind travels with the answer, because a sense without one is on no axis and every rule that
    reads a sense reads an axis. For an assignment, the queue already knew the kind from the sense
    it offered. For a definition, only the person knows it. Either way the Record carries it, so the
    Dictionary that a run consults is reproducible from the Record alone.
    """

    #: The rule that raised the question, and what it raised: the surface a person answered
    #: about.
    rule_id: str
    raised: str
    #: The sense the person named: one the Dictionary already holds, or a new one.
    term: str
    #: The axis that sense sits on.
    theme_kind: str
    #: Where the answer applies. A person deciding for one folder decided for one folder.
    scope: Scope = PROJECT_SCOPE
    #: Whether the person named a sense the engine offered, or stated a new one.
    defined: bool = False
    #: Every other spelling that the names write the word in (``RPM`` beside ``rpm``), where the
    #: answer carried them (a correction does), so the sense reaches those names too.
    spellings: tuple[str, ...] = ()


def answers_in_force(project: Path) -> tuple[QuestionAnswer, ...]:
    """Return the answer currently in force for each question, in canonical order.

    The function silently skips an answer that names no kind. A decision recorded before the kind
    travelled with it cannot be placed on an axis, and guessing an axis would put a person's word on
    a rule's axis they never chose. The skipped answer stays in the log and stays readable.
    """
    found: list[QuestionAnswer] = []
    for target, event in sorted(latest_by_target(read_events(project)).items()):
        if not target.startswith(f"{TARGET_PREFIX}:") or event.kind not in NAMING_KINDS:
            continue
        try:
            payload = json.loads(event.payload)
        except (TypeError, ValueError):
            continue
        term = str(payload.get("answer") or "").strip()
        theme_kind = str(payload.get("theme_kind") or "").strip()
        raised = str(payload.get("raised") or "").strip()
        rule_id = str(payload.get("rule_id") or "").strip()
        if not (term and theme_kind and raised and rule_id):
            continue
        found.append(
            QuestionAnswer(
                rule_id=rule_id,
                raised=raised,
                term=term,
                theme_kind=theme_kind,
                scope=event.scope,
                defined=event.kind is RecordEventKind.DEFINE,
                spellings=_spellings(payload),
            )
        )
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Orphaned:
    """One in-force decision whose basis has gone, and what went.

    ``RecordEvent.orphaned`` is declared and no reader fills it. It cannot be filled: the log is
    append-only and cannot flip a flag on a row it has written. So orphanhood is what a reader
    concludes and never what the log stores. This is that reader for a decision about a question.

    An orphaned decision is reported and still honoured. This is a decision of the design and not an
    omission. A person said what a word means. That does not stop being true because somebody
    switched off the rule that prompted them to say it. A reader needs to see that the question is
    no longer being asked, so that they can withdraw the answer if they want to. Withdrawing is
    itself a decision, appended like any other.
    """

    #: The decision, as the log holds it.
    event: RecordEvent
    #: What has gone: the rule that raised the question this answers.
    missing_rule: str


def terms_in_force(project: Path) -> tuple[QuestionAnswer, ...]:
    """Return what a person said each word means where no rule asked, in canonical order.

    The shape is the same as an answer to a question, so the Dictionary composes both alike. The
    word is what was raised and the sense is what was named. No rule raised it, so the rule is
    empty, and nothing can orphan it (:func:`orphaned_answers` reads questions alone).
    """
    found: list[QuestionAnswer] = []
    for target, event in sorted(latest_by_target(read_events(project)).items()):
        if not target.startswith(f"{TERM_PREFIX}:") or event.kind not in NAMING_KINDS:
            continue
        try:
            payload = json.loads(event.payload)
        except (TypeError, ValueError):
            continue
        term = str(payload.get("answer") or "").strip()
        theme_kind = str(payload.get("theme_kind") or "").strip()
        raised = str(payload.get("raised") or "").strip()
        if not (term and theme_kind and raised):
            continue
        found.append(
            QuestionAnswer(
                rule_id="",
                raised=raised,
                term=term,
                theme_kind=theme_kind,
                scope=event.scope,
                defined=event.kind is RecordEventKind.DEFINE,
                spellings=_spellings(payload),
            )
        )
    return tuple(found)


def _spellings(payload: dict[str, object]) -> tuple[str, ...]:
    """The other spellings an answer carries, as written, or none."""
    held = payload.get("spellings")
    if not isinstance(held, list):
        return ()
    return tuple(sorted({str(item) for item in held if isinstance(item, str) and item.strip()}))


def orphaned_answers(project: Path, *, in_force: Collection[str]) -> tuple[Orphaned, ...]:
    """Return the in-force answers whose raising rule is no longer in the Rulebook.

    ``in_force`` is the identifiers of the rules that a run evaluates, which
    :class:`~ingence.rulebook.resolve.EffectiveRulebook` holds. The caller passes it in, so this
    function reads the Record and nothing else and stays testable from a list.

    Only a rule that has gone counts. A rule that still runs and no longer raises the question is
    the ordinary case and usually means the answer worked. A term that the Dictionary now places is
    a term that nothing asks about. Reading that as an orphan would report every successful answer
    as a loose end.
    """
    rules = frozenset(in_force)
    found: list[Orphaned] = []
    for target, event in sorted(latest_by_target(read_events(project)).items()):
        if not target.startswith(f"{TARGET_PREFIX}:") or event.kind not in NAMING_KINDS:
            continue
        rule_id = target.split(":", 2)[1] if target.count(":") >= 2 else ""
        if rule_id and rule_id not in rules:
            found.append(Orphaned(event=event, missing_rule=rule_id))
    return tuple(found)
