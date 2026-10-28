"""What a verdict word decides, and what is checked before one is written.

A review sheet asks for verdicts in a fixed vocabulary, one word per row. The Candidates view
offers four of the same words as buttons. This module holds the one table that says what each word
decides. The sheet import (``record/sheets.py``) and the Dashboard's one writing route
(``api/routes/decide.py``) both read the table, so a word decides one thing wherever it is said.

| Word | Event | Said about |
|---|---|---|
| ``placeholder`` | as raised | a file ``content_unreadable`` raised: its content is elsewhere |
| ``empty_output`` | as raised | the same: a real file, empty, a run's output that found nothing |
| ``archive_link`` | as raised | the same: a link the archive left, the file on another server |
| ``stub`` | as raised | the older word for ``placeholder``, read from a sheet offering it |
| ``not_stub`` | as raised | the older word for ``empty_output``, the same |
| ``derived`` | confirm | a proposed derivation, in the direction proposed |
| ``reversed`` | reject | the same: the relation is real and runs the other way |
| ``unrelated`` | reject | the same: the two share a name and nothing else |
| ``related`` | reject | a pair or copy class: together, neither made from nor a copy of the other |
| ``one_thing`` | confirm | a class: every member is one thing, or one experiment |
| ``mixed`` | reject | the same: the class joins things that do not belong together |
| ``correct`` | confirm | a stage claim |
| ``wrong`` | reject | the same, carrying ``correct_stage`` where the person named it |
| ``real_contradiction`` | confirm | a file whose bytes are not what its extension says |
| ``container_convention`` | reject | the same: the bytes are the container its format uses |
| ``copies`` | confirm | a class of files agreeing on name, size and both instants |
| ``not_copies`` | reject | the same: distinct files that happen to agree |
| ``cannot_say`` | annotate | anything: counted, and never read as either |
| ``accept`` | confirm | a proposal: a domain pack, or a folder of a person's miscellaneous files |
| ``refuse`` | reject | the same: not loaded, or not miscellaneous files, and not proposed again |

Whether an extension is worth reading is a second, smaller table. The words are a person's teaching
about what a dive should open. They are not a verdict on a rule's output, and ``no`` and ``never``
would otherwise be two rejections of one kind:

| Word | Event | Says of every file carrying the extension |
|---|---|---|
| ``yes`` | confirm | worth a dive's reading |
| ``no`` | reject | read after the unanswered and ``yes`` files, in its family's turn |
| ``never`` | annotate | read after the ``no`` files: a short dive never reaches it |

``never`` is an annotation and not a rejection, because it does not say that something is wrong. It
is what a person wants a dive to remember. Neither word vetoes a reading, because the dive supports
opening any file marked ``never`` or ``no``. Both words order the spend, and nothing is skipped for
them. The word travels in the payload as ``worth_reading``, and the dive reads the word and never
the kind.

A word that names what a file is decides as raised, by what the finding raised. The word
``empty_output`` on a question raised as ``empty_output`` agrees with it. On a question raised as
``empty_and_old`` the same word corrects it. So the same word confirms one question and rejects
another, and :func:`kind_for` reads the kind from the word and the value together. Where no raised
value is known, the older reading stands: a placeholder confirms and an empty output rejects. That
is what ``stub`` and ``not_stub`` meant about the one value that version 2 raised.

The word always travels in the payload, beside the kind, because two rejections can be different
facts. ``reversed`` says the relation exists and ``unrelated`` says it does not. A figure that
cannot tell them apart would count a direction error as a false pair. A note travels wherever one
was given. ``cannot_say`` is an annotation and is counted. In the audit, not deciding is an outcome.

A verdict about something the current run does not hold is refused, on ``decide.py``'s rule. A sheet
drawn from an earlier run or another project would otherwise write decisions about conclusions that
are not there. That is the one way an append-only log can be made to hold nonsense without anybody
editing it. Every identifier is the engine's own and names no run (``core/ids.py``), so an unchanged
conclusion reached again is the same target.

Which words a row is offered is the third table here. A page and a sheet that offered different
words for one kind of row would be two vocabularies for one decision. The table is keyed by the
raising rule's predicate and never its name, so a project revision that renames a rule keeps its
words:

| Row | Predicate | Words, each with ``cannot_say`` |
|---|---|---|
| a question | ``content_unreadable`` | placeholder, empty_output, archive_link |
| a question | ``extension_contradicts_content`` | real_contradiction, container_convention |
| a question | ``domain_pack_would_read`` | accept, refuse (no ``cannot_say`` offered) |
| a question | ``miscellaneous_files_to_approve`` | accept, refuse (no ``cannot_say`` offered) |
| a question | any other flag | correct, wrong |
| a class | ``same_observed_metadata``, ``same_content_digest`` | copies, not_copies |
| a class | any other grouping | one_thing, mixed |
| a claim | any | correct, wrong (with the right value) |
| a pair | any | derived, reversed, related, unrelated |

What a page shows for each word is the fourth table here. :data:`LABELS` holds the words a button
shows. :data:`SUBTEXTS` holds the one short phrase beneath a label, only where the label alone would
mislead. The stored word never changes. ``cannot_say`` is written wherever "Can't say" is clicked,
so a sheet and a page still say one word each.

The word ``related`` has one meaning on every kind it is offered on. It is read wherever its
neighbours are. On a proposed pair, the two belong together and neither was made from the other.
This rejects the derivation as proposed. On a class of copies, the files belong together and are not
copies of one file. This rejects the copy claim, and the page shows it as "Related, not copies"
there. Its payload names the primitive it points to (:data:`RELATED_RELATION`, ``concerns``).
``not_copies`` keeps its stored meaning exactly: distinct files that happen to agree. That is what a
person means by "Unrelated", and a page shows it so. Precision counts every rejection's word apart
(``record/precision.py``).

A set of copies is offered "Copies", "Unrelated" and "Can't say", so the choices stay as clear and
simple as possible. A class still takes the word ``related`` where a returned sheet says it
(:func:`class_takes`), and the word decides what it always decided. A ``related`` stored on a class
reads as "Related, not copies" wherever it is shown (:func:`shown_on`). The Record's stored words
never change meaning.

A proposal's question still takes the words that it was offered before. The question for a pack or
for a person's miscellaneous files offered ``correct / wrong / cannot_say`` until it offered
``accept`` and ``refuse``. A returned sheet that says an old word decides what the word always
decided (:func:`question_takes`).

What a sheet and a page both write is spelled once, here. This covers the subject that a question's
findings make together (:func:`combined_subject`, :func:`question_subject`) and an extension's event
(:func:`extension_counts`, :func:`extension_event`). The two paths cannot drift apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import RecordEventKind
from ingence.record.answers import stored_evidence
from ingence.record.log import append_event, latest_by_target, read_events
from ingence.record.targets import (
    CLASS_PREFIX,
    candidate_target,
    claim_target,
    class_target,
    extension_named_by,
    question_target,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.storage.catalog import Catalog

__all__ = [
    "CANDIDATE_WORDS",
    "CLAIM_WORDS",
    "LABELS",
    "LABELS_BESIDE",
    "NO_CLASS",
    "OBSERVED",
    "RELATED_RELATION",
    "SEVERAL_CLASSES",
    "SEVERAL_VALUES",
    "SUBTEXTS",
    "VERDICTS",
    "WORTH_READING",
    "ClassIndex",
    "Subject",
    "VerdictRefusedError",
    "candidate_subjects",
    "claim_subjects",
    "class_index",
    "class_shown",
    "class_takes",
    "class_words",
    "combined_subject",
    "current_run",
    "extension_counts",
    "extension_event",
    "finding_subjects",
    "folders_not_programs",
    "kept_line",
    "kind_for",
    "kind_of",
    "question_subject",
    "question_takes",
    "question_words",
    "shown_on",
    "shown_word",
    "spelled_key",
    "verdict_payload",
    "word_subtext",
    "worth_reading",
    "write_note",
    "write_observation",
    "write_verdict",
]

#: The table above, as the code reads it.
VERDICTS: dict[str, RecordEventKind] = {
    # "Stub" is retired as the word offered. A placeholder stands for content held elsewhere, and an
    # empty output is a real file that holds nothing. Without a sharp definition, the stub category
    # could become a trap. A sheet that offers the older pair is still read, and the pair decides
    # the same things.
    "placeholder": RecordEventKind.CONFIRM,
    "empty_output": RecordEventKind.REJECT,
    "archive_link": RecordEventKind.CONFIRM,
    "stub": RecordEventKind.CONFIRM,
    "not_stub": RecordEventKind.REJECT,
    "derived": RecordEventKind.CONFIRM,
    "reversed": RecordEventKind.REJECT,
    "unrelated": RecordEventKind.REJECT,
    "one_thing": RecordEventKind.CONFIRM,
    "mixed": RecordEventKind.REJECT,
    "correct": RecordEventKind.CONFIRM,
    "wrong": RecordEventKind.REJECT,
    "real_contradiction": RecordEventKind.CONFIRM,
    "container_convention": RecordEventKind.REJECT,
    "copies": RecordEventKind.CONFIRM,
    "not_copies": RecordEventKind.REJECT,
    # A pair that belongs together, neither made from the other.
    "related": RecordEventKind.REJECT,
    "cannot_say": RecordEventKind.ANNOTATE,
    # A proposal is taken or refused. A pack that a person accepts is loaded, and one that a person
    # refuses is not proposed again, as ``api/routes/proposals.py`` offers them on the page.
    "accept": RecordEventKind.CONFIRM,
    "refuse": RecordEventKind.REJECT,
}

#: The primitive that a ``related`` pair or class points to: ``concerns``, about the same subject. A
#: person who says two files belong together, without either being made from (or a copy of) the
#: other, has said nothing about one mentioning the other. That is ``references``, which a proposing
#: rule already observed where it read a mention. The primitive is recorded in the payload only.
RELATED_RELATION = "concerns"

#: What a person may say about a proposed pair, in the Candidates view and on a sheet alike.
CANDIDATE_WORDS = ("derived", "reversed", "related", "unrelated", "cannot_say")

#: What a person may say about one claim: its value is right, or wrong (naming the right one).
CLAIM_WORDS = ("correct", "wrong", "cannot_say")

#: The third table above: the words a question is offered, by the predicate of the rule raising
#: it. A flag not named here raises something a person calls right or wrong.
_QUESTION_WORDS: dict[str, tuple[str, ...]] = {
    "content_unreadable": ("placeholder", "empty_output", "archive_link", "cannot_say"),
    "extension_contradicts_content": ("real_contradiction", "container_convention", "cannot_say"),
    # Experiments proposed as belonging together: one thing, or not.
    "experiments_may_belong_together": ("one_thing", "mixed", "cannot_say"),
    # A word that nothing binds: the question is what the word is, answered with a meaning
    # (``sense [axis]``). So the words offered beside that are the two that are not one.
    "unbound_word_where_a_kind_stands": ("not_a_word", "cannot_say"),
    # A proposal, taken or refused as the page offers it: a domain pack, or a folder of a person's
    # miscellaneous files.
    "domain_pack_would_read": ("accept", "refuse"),
    "miscellaneous_files_to_approve": ("accept", "refuse"),
}
_ANY_FLAG_WORDS = ("correct", "wrong", "cannot_say")

#: Words a question was offered before and still takes, deciding what they decided then. A
#: proposal's question offered "correct / wrong / cannot_say" in the past, and an imported sheet or
#: answers file may still say one of those words.
_FORMERLY_OFFERED: dict[str, tuple[str, ...]] = {
    "domain_pack_would_read": _ANY_FLAG_WORDS,
    "miscellaneous_files_to_approve": _ANY_FLAG_WORDS,
}

#: The same, for a class: a class of copies is copies or not; any other class is one thing or not.
_COPY_PREDICATES = frozenset({"same_observed_metadata", "same_content_digest"})
_COPY_WORDS = ("copies", "not_copies", "cannot_say")
#: Words that were offered on a class of copies and are still taken. A returned sheet that says one
#: decides what it did, as a proposal's former words do (:data:`_FORMERLY_OFFERED`).
_COPY_WORDS_FORMERLY = ("related",)
_CLASS_WORDS = ("one_thing", "mixed", "cannot_say")


def question_words(predicate: str | None) -> tuple[str, ...]:
    """Return the words a question raised by a rule of this predicate is offered."""
    return _QUESTION_WORDS.get(predicate or "", _ANY_FLAG_WORDS)


def question_takes(predicate: str | None) -> tuple[str, ...]:
    """Return every word a question takes: those it is offered, then those it was offered before."""
    offered = question_words(predicate)
    former = _FORMERLY_OFFERED.get(predicate or "", ())
    return offered + tuple(word for word in former if word not in offered)


def class_words(predicate: str | None) -> tuple[str, ...]:
    """Return the words a class formed by a rule of this predicate is offered."""
    return _COPY_WORDS if (predicate or "") in _COPY_PREDICATES else _CLASS_WORDS


def class_takes(predicate: str | None) -> tuple[str, ...]:
    """Return every word a class takes: those it is offered, then those it was offered before."""
    offered = class_words(predicate)
    return offered + (_COPY_WORDS_FORMERLY if offered == _COPY_WORDS else ())


#: The fourth table above: what a page shows for each word. The stored word is the key and is what
#: every event carries. The label is only what a button, a tag and a row say.
LABELS: dict[str, str] = {
    "placeholder": "Stand-in for a file elsewhere",
    "empty_output": "Empty or blank output file",
    "archive_link": "Archive link to another server",
    "stub": "Stand-in for a file elsewhere",
    "not_stub": "Empty or blank output file",
    "derived": "Made from it",
    "reversed": "The other way round",
    "related": "Related",
    "unrelated": "Unrelated",
    "one_thing": "One group",
    "mixed": "Not one group",
    "correct": "Correct",
    "wrong": "Wrong",
    "real_contradiction": "Not what its extension says",
    "container_convention": "Its format's usual container",
    "copies": "Copies",
    "not_copies": "Unrelated",
    "cannot_say": "Can't say",
    "yes": "Yes",
    "no": "No, read it later",
    "never": "Never, read it last",
    "not_a_word": "Not a word",
    "an_extension": "A file extension",
    "a_code": "A code or identifier",
    "a_program_mark": "A mark a program adds",
    "just_letters": "Letters standing for nothing",
    # A proposal's two answers (``api/routes/proposals.py``).
    "accept": "Accept",
    "refuse": "Refuse",
    # An experiment's analysis is elsewhere, or not (``completeness/elsewhere.py``).
    "elsewhere": "Analyzed elsewhere",
    "not_elsewhere": "Not elsewhere",
}

#: The one short phrase under a label, only where the label alone would mislead. "Related" alone
#: could be read as a derivation after all.
SUBTEXTS: dict[str, str] = {
    "related": "neither made from the other",
}


#: A label that depends on the words beside it. The word ``related``, offered beside ``copies``, is
#: "Related, not copies". It is the one word with one meaning, said for the question asked.
LABELS_BESIDE: dict[tuple[str, str], str] = {("copies", "related"): "Related, not copies"}


def shown_word(word: str, offered: Sequence[str] = ()) -> str:
    """Return what a page shows for a stored word: its label, or the word with spaces.

    ``offered`` is every word the row offers, where a label depends on its neighbours
    (:data:`LABELS_BESIDE`).
    """
    for beside in offered:
        found = LABELS_BESIDE.get((beside, word))
        if found is not None:
            return found
    return LABELS.get(word, word.replace("_", " "))


def shown_on(word: str, target: str) -> str:
    """Return what a page shows for a word stored about a target, said as it was offered there.

    A word stored on a class of copies is read beside the words that class took. The word
    ``related`` there is "Related, not copies", its meaning when it was offered, although a class of
    copies is no longer offered it. Anywhere else the word has its own label.
    """
    copying = (*_COPY_WORDS, *_COPY_WORDS_FORMERLY)
    if target.startswith(f"{CLASS_PREFIX}:") and word in copying:
        return shown_word(word, copying)
    return shown_word(word)


def word_subtext(word: str, offered: Sequence[str] = ()) -> str:
    """The phrase under a label, only where the plain label would mislead: never under a label
    its neighbours already made plain."""
    if shown_word(word, offered) != LABELS.get(word, word.replace("_", " ")):
        return ""
    return SUBTEXTS.get(word, "")


#: The second table above: whether every file carrying one extension is worth a dive's reading.
WORTH_READING: dict[str, RecordEventKind] = {
    "yes": RecordEventKind.CONFIRM,
    "no": RecordEventKind.REJECT,
    "never": RecordEventKind.ANNOTATE,
}


#: Why a verdict on a finding raising several values is refused.
SEVERAL_VALUES = "the finding raises several values, each its own question"

#: Why a class a sheet names cannot be found, or cannot be told apart.
NO_CLASS = "no class in the current run has this key"
SEVERAL_CLASSES = "several classes answer to this key; name the rule, or the folder"


class VerdictRefusedError(Exception):
    """The verdict was not written, and the message says what was wrong with it."""


@dataclass(frozen=True, slots=True)
class Subject:
    """What a verdict is about, found in the current run: its target, rule and particulars.

    ``about`` is what the payload records of the subject beside the word. Examples are the finding a
    question was reached through, a pair's two ends and a claim's value. A later reader of the
    Record sees what the person was judging without the run that held it.
    """

    target: str
    rule_id: str
    rule_version: str
    about: Mapping[str, Any] = field(default_factory=dict)


#: The values of ``content_unreadable`` that each word naming a file's kind agrees with. A value
#: that says only that a file is empty or a link is refined by either word that fits it, and is
#: never contradicted.
_NAMES_WHAT_IT_RAISED: dict[str, frozenset[str]] = {
    "empty_output": frozenset({"empty_output", "empty"}),
    "placeholder": frozenset(
        {
            "empty_and_old",
            "dehydrated_or_archived",
            "empty",
            "archive_link",
            "link_outside_the_root",
            "link_without_a_target",
        }
    ),
    "archive_link": frozenset({"archive_link", "link_outside_the_root"}),
}
_NAMES_WHAT_IT_RAISED["stub"] = _NAMES_WHAT_IT_RAISED["placeholder"]
_NAMES_WHAT_IT_RAISED["not_stub"] = _NAMES_WHAT_IT_RAISED["empty_output"]


#: The words that confirm a pair proposed as related. On a pair proposed as ``concerns``, the two
#: belong together, and it is not known which was made from the other, if either was. ``related``
#: confirms the proposal. ``derived`` and ``reversed`` each confirm it and say which was made from
#: which, stored as the words they are. ``unrelated`` rejects it. On a proposed derivation, every
#: word decides what it always did. A word's meaning is one on every pair.
_CONFIRMS_A_PROPOSED: dict[str, frozenset[str]] = {
    "related": frozenset({RELATED_RELATION}),
    "derived": frozenset({RELATED_RELATION}),
    "reversed": frozenset({RELATED_RELATION}),
}


def kind_for(
    word: str, *, raised: str | None = None, primitive: str | None = None
) -> RecordEventKind:
    """Return what a word decides about a subject that raised this value, where it raised one.

    A word that names what a file is confirms a question whose raised value it agrees with, and
    rejects one that it names differently. A word that names the relation a pair was proposed as
    confirms the pair (``primitive``). Every other word decides what :func:`kind_of` says.
    """
    kind = kind_of(word)
    if primitive is not None and primitive in _CONFIRMS_A_PROPOSED.get(word, frozenset()):
        return RecordEventKind.CONFIRM
    agrees = _NAMES_WHAT_IT_RAISED.get(word)
    if agrees is None or raised is None:
        return kind
    return RecordEventKind.CONFIRM if raised in agrees else RecordEventKind.REJECT


def kind_of(word: str) -> RecordEventKind:
    """Return what a verdict word decides, or refuse a word the table does not hold."""
    kind = VERDICTS.get(word)
    if kind is None:
        known = ", ".join(VERDICTS)
        message = f"{word!r} is not a verdict word. The words are: {known}"
        raise VerdictRefusedError(message)
    return kind


def current_run(catalog: Catalog) -> str:
    """Return the run a verdict is written against, or refuse where there is none."""
    row = catalog.query("SELECT run_id FROM run").fetchone()
    if not row or not row[0]:
        message = "the project has no published run, so there is nothing to decide against"
        raise VerdictRefusedError(message)
    return str(row[0])


def _rows(catalog: Catalog, sql: str, identifiers: list[str]) -> list[tuple[Any, ...]]:
    return list(catalog.query(sql, [identifiers]).fetchall())


def finding_subjects(
    catalog: Catalog, finding_ids: Iterable[str], *, each_value: bool = False
) -> dict[str, Subject | tuple[Subject, ...] | str]:
    """Return the question each finding raised, or why a verdict on it cannot be written.

    A verdict on a finding is a decision about its question: what the rule raised, on every file
    that raised it. A finding that raised several values is several questions. By default, one word
    is refused for it with that reason and is not written against the first.

    With ``each_value``, a row's word decides each of them. A sheet shows the finding as one row
    that names every value it raised, and the person's one word is their judgment of that row. Each
    question is returned with the values it was raised beside (``raised_together``), so the event
    says it was decided as one of several. Where two rows then say different things about one
    question, the import refuses both.
    """
    wanted = sorted(set(finding_ids))
    found: dict[str, Subject | tuple[Subject, ...] | str] = {}
    for finding_id, rule_id, rule_version, subject, raised in _rows(
        catalog,
        "SELECT finding_id, rule_id, rule_version, subject, raised FROM findings "
        "WHERE finding_id IN (SELECT unnest(?::VARCHAR[]))",
        wanted,
    ):
        values = [str(value) for value in (raised or [])]
        if len(values) > 1 and each_value:
            found[str(finding_id)] = tuple(
                Subject(
                    target=question_target(str(rule_id), value),
                    rule_id=str(rule_id),
                    rule_version=str(rule_version),
                    about={
                        "finding_id": str(finding_id),
                        "subject": str(subject),
                        "raised": value,
                        "raised_together": values,
                    },
                )
                for value in values
            )
            continue
        if len(values) != 1:
            found[str(finding_id)] = SEVERAL_VALUES
            continue
        found[str(finding_id)] = Subject(
            target=question_target(str(rule_id), values[0]),
            rule_id=str(rule_id),
            rule_version=str(rule_version),
            about={"finding_id": str(finding_id), "subject": str(subject), "raised": values[0]},
        )
    return found


def combined_subject(first: Subject, found: Sequence[Subject]) -> Subject:
    """One subject for several findings reaching one question: it names every finding.

    A sheet whose rows name several findings of one question writes one decision about the question,
    naming every finding and file it came from. The Dashboard's question page writes the same
    decision about every finding the question covers. One function serves both, so the two are one
    spelling.
    """
    if len(found) == 1 or "finding_id" not in first.about:
        return first
    about = {
        key: value
        for key, value in first.about.items()
        if key not in ("finding_id", "subject", "raised_together")
    }
    about["finding_ids"] = sorted({str(item.about["finding_id"]) for item in found})
    about["subjects"] = sorted({str(item.about["subject"]) for item in found})
    together = sorted(
        {str(value) for item in found for value in item.about.get("raised_together", ())}
    )
    if together:
        about["raised_together"] = together
    return Subject(first.target, first.rule_id, first.rule_version, about)


def question_subject(catalog: Catalog, rule_id: str, raised: str) -> Subject | None:
    """Return the subject of a verdict about a whole question, or None where the run raises none.

    Exactly what a sheet naming every finding of the question writes: each finding read as
    :func:`finding_subjects` reads it with ``each_value``, the ones about this question kept, and
    the lot combined by :func:`combined_subject`. The Dashboard's unit is the question, so its
    verdict covers every file that asked it.
    """
    identifiers = [
        str(row[0])
        for row in catalog.query(
            "SELECT DISTINCT f.finding_id FROM findings f, unnest(f.raised) AS u(value) "
            "WHERE f.rule_id = ? AND u.value = ? ORDER BY 1",
            [rule_id, raised],
        ).fetchall()
    ]
    target = question_target(rule_id, raised)
    subjects: list[Subject] = []
    for found in finding_subjects(catalog, identifiers, each_value=True).values():
        about = (
            found if isinstance(found, tuple) else (found,) if isinstance(found, Subject) else ()
        )
        subjects.extend(subject for subject in about if subject.target == target)
    if not subjects:
        return None
    subjects.sort(key=lambda subject: str(subject.about.get("finding_id", "")))
    return combined_subject(subjects[0], subjects)


def candidate_subjects(catalog: Catalog, candidate_ids: Iterable[str]) -> dict[str, Subject]:
    """Return each proposed pair the current run holds, as the target a verdict names.

    The pair's evidence travels with the answer (``record/answers.py``). The evidence is the
    proposing rule and what it read, so a later run proposes the pair again only where that changed.
    """
    wanted = sorted(set(candidate_ids))
    evidence = stored_evidence(catalog, wanted)
    return {
        str(candidate_id): Subject(
            target=candidate_target(str(candidate_id)),
            rule_id=str(rule_id),
            rule_version=str(rule_version),
            about={
                "source_id": str(source_id),
                "target_id": str(target_id),
                "decision_tier": str(tier),
                # What the pair was proposed as, which ``related`` confirms or not.
                "primitive": str(primitive),
                "evidence": evidence.get(str(candidate_id), {}),
            },
        )
        for candidate_id, rule_id, rule_version, source_id, target_id, tier, primitive in _rows(
            catalog,
            "SELECT candidate_id, rule_id, rule_version, source_id, target_id, decision_tier, "
            "primitive FROM candidates WHERE candidate_id IN (SELECT unnest(?::VARCHAR[]))",
            wanted,
        )
    }


def claim_subjects(catalog: Catalog, claim_ids: Iterable[str]) -> dict[str, Subject]:
    """Return each claim the current run holds, as the target a verdict names."""
    wanted = sorted(set(claim_ids))
    return {
        str(claim_id): Subject(
            target=claim_target(str(claim_id)),
            rule_id=str(rule_id),
            rule_version=str(rule_version),
            about={
                "subject": str(subject),
                "object": None if value is None else str(value),
                "resolution_status": str(status),
            },
        )
        for claim_id, rule_id, rule_version, subject, value, status in _rows(
            catalog,
            "SELECT claim_id, rule_id, rule_version, subject, object_reference, "
            "resolution_status FROM claims WHERE claim_id IN (SELECT unnest(?::VARCHAR[]))",
            wanted,
        )
    }


def spelled_key(key: str) -> str:
    """Return a class key as a person reads it: its components as ``slot:value``, space-joined.

    A name rule's key is its kept components, as a list of pairs or under ``components`` beside
    the folder the class was formed in. A person reading a sheet sees the components and not the
    JSON, so that spelling is what a sheet may name a class by. Any other key is its own spelling.
    """
    try:
        parsed = json.loads(key)
    except (TypeError, ValueError):
        return key
    pairs = parsed.get("components") if isinstance(parsed, dict) else parsed
    if not isinstance(pairs, list) or not all(
        isinstance(pair, list) and len(pair) == 2 for pair in pairs
    ):
        return key
    return " ".join(f"{slot}:{value}" for slot, value in pairs)


@dataclass(frozen=True, slots=True)
class ClassIndex:
    """Every class the current run's grouping rules claimed, findable by key or by spelling.

    ``folders`` holds, for a key formed within one folder, that folder's path. A name identity is
    compared within one folder, so two folders can hold classes of one spelling, and only the folder
    tells them apart.
    """

    by_key: Mapping[str, tuple[str, ...]]
    by_spelling: Mapping[str, tuple[tuple[str, str], ...]]
    versions: Mapping[str, str]
    folders: Mapping[str, str]

    def find(self, named: str, *, rule_id: str | None, folder: str | None) -> Subject | str:
        """Return the class a sheet names, or why it names none or more than one."""
        matches = [(rule, named) for rule in self.by_key.get(named, ())]
        if not matches:
            matches = list(self.by_spelling.get(named, ()))
        if rule_id is not None:
            matches = [match for match in matches if match[0] == rule_id]
        if len(matches) > 1 and folder:
            matches = [match for match in matches if self.folders.get(match[1]) == folder]
        if not matches:
            return NO_CLASS
        if len(matches) > 1:
            return SEVERAL_CLASSES
        ((rule, key),) = matches
        return Subject(
            target=class_target(rule, key),
            rule_id=rule,
            rule_version=self.versions.get(rule, ""),
            about={"key": key},
        )


def class_index(catalog: Catalog) -> ClassIndex:
    """Read every class the current run's grouping rules claimed into a :class:`ClassIndex`."""
    from ingence.dictionary.written import written_forms, written_text

    by_key: dict[str, set[str]] = {}
    by_spelling: dict[str, set[tuple[str, str]]] = {}
    versions: dict[str, str] = {}
    # A class is also found by the spelling that a page shows. A multi-part word is shown as its
    # files wrote it (``word:U2OS``), and the key's spelling stays the parts. The shown spelling is
    # added to every class it belongs to, as the key's own is. So the folder that a sheet names
    # still tells two classes of one spelling apart, and a spelling that fits several classes is
    # refused as several. It never takes another class's own spelling.
    written = written_forms(catalog)
    keyed = catalog.query(
        "SELECT DISTINCT rule_id, rule_version, object_reference FROM claims "
        # A class published as conflicting is still a class that a person was shown. A defeated
        # claim publishes as conflicting, and a verdict on it must not be refused as "no class has
        # this key".
        "WHERE predicate LIKE 'groups.%' "
        "AND resolution_status IN ('resolved', 'examined_conflicting') "
        "AND object_reference IS NOT NULL ORDER BY rule_id, object_reference"
    ).fetchall()
    for rule_id, rule_version, key in keyed:
        by_key.setdefault(str(key), set()).add(str(rule_id))
        by_spelling.setdefault(spelled_key(str(key)), set()).add((str(rule_id), str(key)))
        versions[str(rule_id)] = str(rule_version)
    natural = set(by_spelling)
    for rule_id, _, key in keyed:
        shown = written_text(spelled_key(str(key)), written)
        if shown not in natural:
            by_spelling.setdefault(shown, set()).add((str(rule_id), str(key)))
    folder_of: dict[str, str] = {}
    for key in by_key:
        try:
            parsed = json.loads(key)
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("folder"), str):
            folder_of[key] = parsed["folder"]
    paths = dict(
        catalog.query(
            "SELECT entry_id, relative_path FROM entries "
            "WHERE entry_id IN (SELECT unnest(?::VARCHAR[]))",
            [sorted(set(folder_of.values()))],
        ).fetchall()
    )
    return ClassIndex(
        by_key={key: tuple(sorted(rules)) for key, rules in by_key.items()},
        by_spelling={name: tuple(sorted(found)) for name, found in by_spelling.items()},
        versions=versions,
        folders={
            key: (str(paths[entry]) or ".") for key, entry in folder_of.items() if entry in paths
        },
    )


def class_shown(catalog: Catalog, subject: Subject) -> Subject:
    """A class's subject with the files the person was shown: its members in the current run.

    An answer on a set is about the files the set held when it was answered. A copies key names no
    place, so a file that arrives later can join the class, and no person was shown it. The sorted
    entry identifiers travel in the payload (``members``), and inference applies the answer to them
    alone (``record/answers.py``).
    """
    key = subject.about.get("key")
    if key is None or "members" in subject.about:
        return subject
    # Only a set of copies. Inference reads no other class's answer, and a group answered "One
    # group" takes in a new replicate unasked.
    predicate = catalog.query(
        "SELECT predicate FROM rules WHERE rule_id = ? LIMIT 1", [subject.rule_id]
    ).fetchone()
    if predicate is None or str(predicate[0] or "") not in _COPY_PREDICATES:
        return subject
    members = sorted(
        str(row[0])
        for row in catalog.query(
            "SELECT DISTINCT subject FROM claims WHERE rule_id = ? AND object_reference = ? "
            "AND predicate LIKE 'groups.%' "
            "AND resolution_status IN ('resolved', 'examined_conflicting')",
            [subject.rule_id, str(key)],
        ).fetchall()
    )
    return Subject(
        subject.target, subject.rule_id, subject.rule_version, {**subject.about, "members": members}
    )


def verdict_payload(
    subject: Subject,
    word: str,
    *,
    note: str | None = None,
    correct_stage: str | None = None,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return what a verdict event carries: the word, the rule, the subject, and what was said.

    The rule and its version are recorded because a candidate's and a claim's target carry
    neither, and a precision figure is a figure about one rule at one version.
    """
    payload: dict[str, Any] = {
        "verdict": word,
        "rule_id": subject.rule_id,
        "rule_version": subject.rule_version,
        **dict(subject.about),
    }
    if correct_stage and correct_stage.strip():
        if word != "wrong":
            message = (
                f"a stage is named only beside 'wrong', and {word!r} was given with one; a "
                "confirmation carrying a different stage would read as a correction"
            )
            raise VerdictRefusedError(message)
        payload["correct_stage"] = correct_stage.strip()
    if word == "related":
        payload["relation"] = RELATED_RELATION
    if note and note.strip():
        payload["note"] = note.strip()
    if source:
        payload["source"] = dict(source)
    return payload


def write_verdict(
    project: Path,
    subject: Subject,
    word: str,
    *,
    actor: str,
    occurred_at: str,
    against_run_id: str,
    note: str | None = None,
    correct_stage: str | None = None,
    source: Mapping[str, Any] | None = None,
) -> RecordEvent:
    """Append one verdict to the Record, against the run named, and return the event as stored."""
    raised = subject.about.get("raised")
    proposed = subject.about.get("primitive")
    kind = kind_for(
        word,
        raised=str(raised) if raised is not None else None,
        primitive=str(proposed) if proposed is not None else None,
    )
    return append_event(
        project,
        kind=kind,
        actor=actor,
        occurred_at=occurred_at,
        target=subject.target,
        against_run_id=against_run_id,
        payload=verdict_payload(
            subject, word, note=note, correct_stage=correct_stage, source=source
        ),
        scope=PROJECT_SCOPE,
    )


def write_note(
    project: Path,
    subject: Subject,
    note: str,
    *,
    actor: str,
    occurred_at: str,
    against_run_id: str,
    source: Mapping[str, Any] | None = None,
) -> RecordEvent:
    """Append a person's note about a subject, deciding nothing.

    A note without a verdict is kept. A row that a person left without a word but wrote on is their
    words about that subject, and dropping it would lose the one thing they said. It is a ``note``
    event, which is never the decision in force. A note written after a verdict leaves the verdict
    standing (``latest_by_target``).
    """
    said = note.strip()
    if not said:
        message = "a note says something; an empty note records nothing"
        raise VerdictRefusedError(message)
    payload: dict[str, Any] = {
        "note": said,
        "rule_id": subject.rule_id,
        "rule_version": subject.rule_version,
        **dict(subject.about),
    }
    if source:
        payload["source"] = dict(source)
    return append_event(
        project,
        kind=RecordEventKind.NOTE,
        actor=actor,
        occurred_at=occurred_at,
        target=subject.target,
        against_run_id=against_run_id,
        payload=payload,
        scope=PROJECT_SCOPE,
    )


#: Where an observation's terms travel in its payload: a list, each entry naming the words as
#: written, the sense and its axis.
OBSERVED = "observed"


def kept_line(payload: Mapping[str, Any]) -> str | None:
    """An observation's terms as a person reads them in the Record: "kept: brisa (an organism)".

    A term kept from a note has no ``note`` of its own. Without this line, the row's Record would
    read "note ...: null". So every page that lists notes says this line where the note is absent.
    """
    terms = payload.get(OBSERVED)
    if not isinstance(terms, list) or not terms:
        return None
    said = []
    for term in terms:
        if not isinstance(term, dict) or not term.get("text"):
            continue
        kind = str(term.get("theme_kind") or "").replace("_", " ").strip()
        article = "an" if kind[:1].lower() in "aeiou" else "a"
        said.append(f"{term['text']} ({article} {kind})" if kind else str(term["text"]))
    return f"kept: {'; '.join(said)}" if said else None


def write_observation(
    project: Path,
    subject: Subject,
    observed: Sequence[Mapping[str, str]],
    *,
    actor: str,
    occurred_at: str,
    against_run_id: str,
    source: Mapping[str, Any] | None = None,
) -> RecordEvent:
    """Append what a person recognised in their note as a term about a subject.

    The chip is what a person clicked beside a note. It is a term that the engine's own name reader
    found in what they typed ("brisa: a sample"), kept with the question, class, claim or pair being
    asked about. It is a ``note`` event, which decides nothing and is never the decision in force. A
    verdict written before or after it stands. Its payload names each term and its sense under
    :data:`OBSERVED`. No reader of the Record reads it, because inference reads no note, and the
    chip says so.
    """
    terms = [
        {
            "text": str(item.get("text") or "").strip(),
            "term": str(item.get("term") or "").strip(),
            "theme_kind": str(item.get("theme_kind") or "").strip(),
        }
        for item in observed
    ]
    if not terms or not all(term["text"] and term["term"] for term in terms):
        message = "an observation names each term as written and the sense it was read as"
        raise VerdictRefusedError(message)
    payload: dict[str, Any] = {
        OBSERVED: terms,
        "rule_id": subject.rule_id,
        "rule_version": subject.rule_version,
        **dict(subject.about),
    }
    if source:
        payload["source"] = dict(source)
    return append_event(
        project,
        kind=RecordEventKind.NOTE,
        actor=actor,
        occurred_at=occurred_at,
        target=subject.target,
        against_run_id=against_run_id,
        payload=payload,
        scope=PROJECT_SCOPE,
    )


def extension_counts(catalog: Catalog) -> dict[str, tuple[int, int]]:
    """Return, per extension as the census stores it, the files carrying it and their bytes.

    What an extension decision records as the run it was made against (``as_shown``): a word
    about thousands of files is different evidence from the same word about two.
    """
    return {
        str(extension or ""): (int(files), int(size or 0))
        for extension, files, size in catalog.query(
            "SELECT extension, count(*), sum(size_bytes) FROM entries "
            "WHERE entry_type = 'file' AND entry_kind = 'filesystem' GROUP BY extension"
        ).fetchall()
    }


def extension_event(
    extension: str,
    said: Mapping[str, str | None],
    *,
    files: int,
    size: int,
    source: Mapping[str, Any] | None = None,
) -> tuple[RecordEventKind, dict[str, Any]]:
    """Return the kind and payload of what a person said about one extension.

    ``said`` holds ``worth_reading``, ``described``, ``belongs_to`` and ``note``, each the person's
    words or nothing. The word decides the kind (``WORTH_READING``). Words about what the extension
    is, with no word about reading it, teach and are an annotation. A bare note decides nothing and
    is a note. A sheet row and a page row both come here.
    """
    worth = said.get("worth_reading")
    taught = said.get("described") or said.get("belongs_to")
    if worth:
        kind = WORTH_READING[worth]
    else:
        kind = RecordEventKind.ANNOTATE if taught else RecordEventKind.NOTE
    payload: dict[str, Any] = {
        "extension": extension,
        **{name: value for name, value in said.items() if value},
        "as_shown": {"files": files, "bytes": size},
    }
    if source:
        payload["source"] = dict(source)
    return kind, payload


def worth_reading(project: Path) -> dict[str, str]:
    """Return what a person has said about opening each extension, by extension.

    This is the other half of the extension sheet. Importing its verdicts recorded, per extension,
    whether files carrying it are worth a dive's reading. This function reads them back so that a
    dive can honour them.

    Keys are extensions as the census stores them: lower-cased, no dot, and ``""`` for a file with
    none. Values are the words a person chose, ``yes``, ``no`` or ``never``, and not the event kinds
    they were stored as. The dive's policy is written in the person's vocabulary and not in the
    Record's.

    Only the latest decision about each extension counts, and every earlier one stays in the log. A
    person who changes their mind about opening a format has the run change with them, and can see
    that they changed it.
    """
    spoken = {kind: word for word, kind in WORTH_READING.items()}
    answers: dict[str, str] = {}
    for target, event in latest_by_target(read_events(project)).items():
        extension = extension_named_by(target)
        if extension is None:
            continue
        word = spoken.get(event.kind)
        if word is not None:
            answers[extension] = word
    return answers


#: The word a person says of a folder that the rules read as an installed program or a package.
NOT_A_PROGRAM = "not_a_program"


def folders_not_programs(events: Sequence[RecordEvent]) -> frozenset[str]:
    """Return the entry identifiers of the folders a person said are not programs.

    The function reads the Record the way an approved group is read. A folder counts while the
    latest decision on its program target rejects it. A later confirmation (it is a program after
    all) puts it back, and nothing is guessed. Inference and the dive both read this, so a folder
    that a person took out of the program scope is out for both.
    """
    from ingence.record.targets import PROGRAM_PREFIX

    prefix = f"{PROGRAM_PREFIX}:"
    return frozenset(
        target[len(prefix) :]
        for target, event in latest_by_target(events).items()
        if target.startswith(prefix) and event.kind is RecordEventKind.REJECT
    )
