"""The review pages: every review sheet's kind as a page, and each question made plain.

The Dashboard is clear about what it asks. A review can happen here, with the workbook kept as an
export and import of the same Record events. So each kind of row that a sheet carried is a
page, and every row writes the event that its sheet's import writes (``decide.decide_target``):

| Page | A row is | Words |
|---|---|---|
| ``questions`` | what a flag raised, wherever it raised it | per rule, or a meaning |
| ``classes`` | a class a grouping rule formed, with its members | copies, or one thing |
| ``claims`` | one conclusion, sampled across folders | correct, or wrong and what |
| ``candidates`` | one proposed pair | derived, reversed, unrelated |
| ``extensions`` | the files of an extension no type names | worth reading, what it is |

Every row carries the target that the Record spells for it (``record/targets.py``), the decision
in force on it, and every note written about it, and every row takes a note. Each page says how
far its review has got, answered and remaining, and filters by the top-level folder of the files
a row is about and by rule.

An answered row leaves the page, so a person can focus on the remaining issues. A row is answered
when a decision is in force on its target (``record/log.latest_by_target``). A note decides nothing,
so a row with only notes stays. The page leaves answered rows out unless asked for them, and says
how many it left out. The progress still counts every row. The decision in force is read from the
Record on every request, so a row leaves the moment its answer is written and stays gone after a
reload.

A question about a word is made plain (:func:`word_page`). The page shows at most fifteen of the
names carrying the word, chosen to span its folders, each with the word marked inside it and the
rest of the name quiet. It also shows which folders the word sits in and how many files in all.
The page suggests no meaning. The page asks, the person answers, and whatever they write is
stored as they wrote it.

A word that nothing binds is asked once, as a word. The rule that raises such words is asked from
the word-question page (:mod:`ingence.textword.questions`), with one row per word and every place
it stands. "Only where it stands" is offered on a question about a word only where the route that
writes the answer would keep the meaning to a folder.

A program is one row (:func:`programs`). The row has an installed program's outermost folder ("a
copy of Fiji, N files"), its parts beneath it, and the questions raised only inside it. The Review
queue sets those questions aside (``review.py``), and they are never mixed into the data's
questions. Classes and claims that lie wholly inside a program are left off the pages for the same
reason and counted.

Nothing here writes. An answer imported from a file says which file
(``record/imported.imported_from``), in neutral words, whoever wrote it.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.api.name_patterns import name_pattern
from ingence.api.plain_words import value_sentence
from ingence.api.question_words import ALIKE_BY, TIER_WORDS, copies_reason, question_words_for
from ingence.api.routes.decide import COMPUTER_READINGS
from ingence.api.routes.groupings import (
    EXPERIMENT_GROUPS_PREDICATE,
    Experiments,
    experiment_folders,
    shared_by,
)
from ingence.api.routes.review import review
from ingence.api.routes.views import _outermost
from ingence.dictionary.file_types import EXTENSION_KINDS, file_type_scope
from ingence.dictionary.programs import program_layouts
from ingence.dictionary.store import DictionaryStore
from ingence.dictionary.written import written_forms, written_terms, written_text, written_word
from ingence.grouping.asked_again import AskedAgain, ProposalMembers
from ingence.grouping.lattice_words import WordMap, run_words_as
from ingence.record.answers import (
    answers_read,
    carried_confirmations,
    evidence_matches,
    stored_evidence,
)
from ingence.record.imported import imported_from
from ingence.record.log import latest_by_target, notes_by_target, read_events
from ingence.record.targets import (
    candidate_target,
    claim_target,
    class_target,
    extension_target,
    question_target,
)
from ingence.record.verdicts import (
    CANDIDATE_WORDS,
    CLAIM_WORDS,
    OBSERVED,
    WORTH_READING,
    class_words,
    folders_not_programs,
    kept_line,
    question_words,
    shown_on,
    shown_word,
    spelled_key,
    word_subtext,
)
from ingence.rulebook.predicates.answered import ANSWERED_COPIES_PREDICATE
from ingence.storage.own_files import filesystem_entry
from ingence.textword.questions import PREDICATE as WORD_QUESTION_PREDICATE
from ingence.textword.questions import (
    choices_for,
    declared_word_rules,
    raised_folders,
    scope_where_it_stands,
    word_question_page,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.storage.catalog import Catalog
    from ingence.textword.questions import RaisedFolder

__all__ = [
    "CLAIMS_AT_LEAST",
    "CLAIMS_AT_MOST",
    "DIVIDE_SHARE",
    "KINDS",
    "LIMIT",
    "MEANINGS",
    "PARTS_COVER",
    "PART_SHARE",
    "TOP_LEVEL",
    "VALUE_FILES_LISTED",
    "VALUE_GROUP_PREFIX",
    "WORD_SAMPLE",
    "claim_value",
    "computer_shape",
    "forget_held",
    "page",
    "page_asking",
    "programs",
    "raised_by_page",
    "representative_sample",
    "waiting",
    "word_page",
]

#: The pages, one per kind of review sheet.
KINDS = ("questions", "classes", "claims", "candidates", "extensions")

#: Rows on one page. The progress and the filters count every row. A page shows these.
LIMIT = 200

#: The pages whose rows are built once per settled run. Building the Groupings page's whole list
#: for every page of it takes tens of seconds a page on a large project, at every offset.
#: Its rows depend on the run alone and its answers on the Record alone. So the rows are built
#: once per run read and held, and their answers are read once per Record state (its last
#: sequence and length) onto copies, which a request filters and pages. A new run builds them
#: again, and a new answer reads the answers again, in a fraction of a build. A timer never
#: rebuilds them. The other pages build theirs on every request.
HELD_KINDS = frozenset({"classes"})
#: By (project, kind): the run, its rows as built, and what came beside them ... (and whether
#: each set's names are written yet: a count leaves them to the page). The key does not include
#: the order asked: the Groupings page's build reads none, so one list serves every order.
_HELD_BUILT: dict[tuple[str, str], tuple[str, list[Any], Any, bool]] = {}
#: ... and the run, the Record's state, and the rows with their answers read.
_HELD: dict[tuple[str, str], tuple[str, tuple[int, int], list[Any], Any]] = {}
_HELD_GUARD = threading.Lock()
#: One build at a time for one page: a second request waits for the first's rows, never builds
#: them beside it (the tabs' counts and the page a person opened ask at once).
_HELD_BUILDING: dict[tuple[str, str], threading.Lock] = {}
#: How many projects' rows are held at once. A server serves one project, and tests and a
#: long-lived process open many. The project asked longest ago is let go first. Two copies of a
#: large project's list are tens of megabytes, so a few at most.
HELD_PROJECTS = 2

#: How many names a question about a word shows: enough to see how it is used, few enough to
#: read. Chosen round-robin over the folders it sits in, so fifteen names from one folder never
#: hide the other forty.
WORD_SAMPLE = 15

#: How many folders a question about a word lists by name. The rest are counted.
FOLDERS_SHOWN = 30

#: How many files a claim sample shows for one value is sized to the value. A value's files are
#: divided into parts: the branch they lie under, counted from the deepest folder that holds all
#: of them, and their extension. One file is shown from every part that holds at least
#: :data:`PART_SHARE` of them, and from further parts, largest first, until the parts shown hold
#: :data:`PARTS_COVER` of them (:func:`representative_sample`). The sample is never fewer than
#: this, or all of a value's files where it has fewer. Three files are enough where three is
#: representative for that claim ...
CLAIMS_AT_LEAST = 3

#: ... and never more than this for one value, because a person reads every row.
CLAIMS_AT_MOST = 10

#: A part of a value's files holding at least this share of them is always shown. A rule that
#: goes wrong goes wrong on a part -- one experiment's naming, one kind of file -- and a part of
#: this size is one a person would want to have seen before calling the value right.
PART_SHARE = 0.1

#: Parts are shown, largest first, until together they hold at least this share of the value's
#: files, so a value spread thinly over many folders, none of them a tenth, is judged from more
#: than three of them. The thresholds were set against how the values of two stored censuses
#: spread. They shape a page and decide nothing about a file.
PARTS_COVER = 0.5

#: A part holding more than this share of a value's files is divided by the folders beneath
#: it, so one folder holding most of a value (a person's one data folder) is not one part.
DIVIDE_SHARE = 0.5

#: How many members a class lists. The count is exact. The list is for looking.
MEMBERS_SHOWN = 40

#: What a row about a file at the root of a data pool gives as its top-level folder.
TOP_LEVEL = "(top level)"

#: Every word a row may be answered with, explained, for a button's hover and a question file.
#: The button itself shows the word's label (``record/verdicts.LABELS``), and a phrase beneath it
#: only where the label alone would mislead (``SUBTEXTS``).
MEANINGS: dict[str, str] = {
    "placeholder": "a stand-in: its contents are kept somewhere else",
    "empty_output": "a real file that holds nothing, like a run that found nothing",
    "archive_link": "a link the archive left: the file itself is on another server",
    "stub": "a stand-in: its contents are kept somewhere else",
    "not_stub": "a real file that holds nothing",
    "derived": "the second was made from the first",
    "reversed": "the first was made from the second",
    "related": "they belong together, and neither was made from, nor is a copy of, the other",
    "unrelated": "they share a name and nothing else",
    "one_thing": "every member belongs to one group, or one experiment",
    "mixed": "it joins things that do not belong together",
    "correct": "right as it stands",
    "wrong": "not right (say what it should be)",
    "real_contradiction": "the contents really are not what the extension says",
    "container_convention": "the contents are the container this format is built on",
    "copies": "copies of one file",
    "not_copies": "different files that happen to agree",
    "cannot_say": "none of these; it decides nothing and is counted",
    "accept": "yes: take what is proposed",
    "refuse": "no, and stop proposing it",
    "yes": "worth opening",
    "no": "read after the rest of its kind",
    "never": "read last: a short dive never reaches it",
    "not_a_word": "not a word: an identifier, a code or a fragment of one",
    # What a string that is not a person's word is to a computer.
    "an_extension": "a file extension, written inside the name: the kind of file it was made "
    "from or is about",
    "a_code": "a code: an identifier, a label, a serial, a model or a sample code",
    "a_program_mark": "a mark a program writes: a prefix or suffix it adds to what it saves",
    "just_letters": "letters that stand for nothing on their own: a fragment or a leftover",
}


def _rows(
    catalog: Catalog, sql: str, parameters: Sequence[Any] | None = None
) -> list[dict[str, Any]]:
    cursor = catalog.query(sql, list(parameters or []))
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _words(offered: Iterable[str]) -> list[dict[str, str]]:
    """Each word a row offers: the stored word, what its button shows, and what it means.

    The page shows ``label`` and, beneath it, ``meaning`` only where the label alone would
    mislead. ``explained`` is the longer phrase, for a hover and a question file. The word sent
    back is always the stored one.
    """
    words = tuple(offered)
    return [
        {
            "word": word,
            "label": shown_word(word, words),
            "meaning": word_subtext(word, words),
            "explained": MEANINGS.get(word, word.replace("_", " ")),
        }
        for word in words
    ]


def _top(path: str | None, entry_type: str | None) -> str:
    """The first component of a path: the top-level folder a row's file lies in."""
    spelled = str(path or "")
    if "/" in spelled:
        return spelled.split("/", 1)[0]
    if spelled and entry_type == "directory":
        return spelled
    return TOP_LEVEL


#: The same, in SQL, over an ``entries`` row aliased ``e``.
_TOP_SQL = (
    "CASE WHEN strpos(e.relative_path, '/') > 0 THEN split_part(e.relative_path, '/', 1) "
    "WHEN e.entry_type = 'directory' AND e.relative_path <> '' THEN e.relative_path "
    f"ELSE '{TOP_LEVEL}' END"
)

#: Whether an ``entries`` row aliased ``e`` lies in one of the regions bound as ``?``.
_BENEATH_SQL = (
    "EXISTS (SELECT 1 FROM (SELECT unnest(?::VARCHAR[]) AS region) p "
    "WHERE e.relative_path = p.region OR starts_with(e.relative_path, p.region || '/'))"
)


def _program_folders(catalog: Catalog) -> list[dict[str, Any]]:
    """Every folder a rule read as an installed program's, with what it raised (its names)."""
    return [
        {
            "folder": row["entry_id"],
            "path": str(row["relative_path"] or ""),
            "rule_id": row["rule_id"],
            "raised": list(row["raised"] or []),
            "reason": row["reason"],
        }
        for row in _rows(
            catalog,
            """
            SELECT f.rule_id, f.raised, f.reason, e.relative_path, e.entry_id
            FROM findings f JOIN entries e ON 'folder:' || e.entry_id = f.subject
            WHERE f.subject_kind = 'scope' AND f.rule_id LIKE '%installed_software%'
            ORDER BY e.relative_path, f.rule_id
            """,
        )
    ]


def _regions(catalog: Catalog) -> list[str]:
    """The outermost folders read as a program's: what a row wholly inside is left off for."""
    return _outermost(str(item["path"]) for item in _program_folders(catalog))


# ---------------------------------------------------------------------------
# The decision in force on each row, and what was said about it.
# ---------------------------------------------------------------------------


def _source_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    source = payload.get("source")
    return source if isinstance(source, dict) else {}


def _in_force(event: RecordEvent | None) -> dict[str, Any] | None:
    """The decision in force, as a row shows it: the word, who, when, and any file it came from."""
    if event is None:
        return None
    payload = json.loads(event.payload or "{}")
    word = (
        payload.get("verdict")
        or payload.get("worth_reading")
        or payload.get("answer")
        or event.kind.value
    )
    source = _source_of(payload)
    return {
        "kind": event.kind.value,
        "word": word,
        # What the page shows for the stored word: "Can't say" for ``cannot_say``, and a word
        # read as it was offered where it was stored ("Related, not copies" on a class).
        "label": shown_on(str(word), event.target),
        "actor": event.actor,
        "occurred_at": event.occurred_at,
        "sequence": event.sequence,
        "correct_value": payload.get("correct_stage"),
        "theme_kind": payload.get("theme_kind"),
        "described": payload.get("described"),
        "belongs_to": payload.get("belongs_to"),
        "note": payload.get("note"),
        "reading": payload.get("reading"),
        "imported_from": imported_from(source),
        # Who put an answer under another person's name in the Record.
        "imported_by": source.get("imported_by"),
    }


def answer_waits(held: Mapping[str, Any] | None, watermark: int | None) -> bool:
    """Whether a row with this decision in force (``None``: none) is still waiting on Re-run: it has
    no answer, or its Record sequence lies above what the run read (``watermark``). A run that
    recorded none read no answer, so every answer waits."""
    if not held:
        return True
    sequence = held.get("sequence")
    return watermark is None or sequence is None or int(sequence) > watermark


def _notes(events: Sequence[RecordEvent]) -> list[dict[str, Any]]:
    """Every note on a row: its words, or, for a term kept from a note, what was kept."""
    listed = []
    for event in events:
        payload = json.loads(event.payload or "{}")
        listed.append(
            {
                "actor": event.actor,
                "occurred_at": event.occurred_at,
                "note": payload.get("note") or kept_line(payload),
                "observed": payload.get(OBSERVED) or [],
            }
        )
    return listed


def _decorate(
    rows: list[dict[str, Any]],
    project: Path,
    events: Sequence[RecordEvent] | None = None,
    words: Callable[[], WordMap] | None = None,
    members_now: Callable[[str], frozenset[str] | None] | None = None,
) -> None:
    """Give every row the decision in force on its target and every note written about it.

    ``events`` is the Record already read, where a caller reads it once for several pages.
    ``words`` makes the run's map of spellings to terms, where the rows are the run's questions.
    The function finds a group question that was answered before the lattice's words changed by
    what it asks, and does not ask it again (:class:`~ingence.grouping.asked_again.AskedAgain`). A
    proposed group is found only by its members, and ``members_now`` says those of a proposal as
    the run raises it.
    """
    events = read_events(project) if events is None else events
    in_force = latest_by_target(events)
    noted = notes_by_target(events)
    # An answer written on a proposal's own target names the question it answered
    # (``record/proposals.py``: a group's core, a folder's miscellany or program answer, a
    # pack), so the question's row reads it as its decision.
    answered: dict[str, RecordEvent] = {}
    for event in in_force.values():
        try:
            said = json.loads(event.payload) if event.payload else {}
        except (TypeError, ValueError):
            continue
        question = said.get("question") if isinstance(said, dict) else None
        if isinstance(question, str):
            answered[question] = event
    members_then = ProposalMembers(project)
    again = (
        None
        if words is None
        else AskedAgain(
            words,
            {**answered, **in_force},
            rank=lambda event: event.sequence,
            members_then=lambda target, event: members_then.of(str(event.against_run_id), target),
            members_now=members_now,
        )
    )
    for row in rows:
        # A proposal is answered on a target of its own (a pack, a person's miscellaneous files, a
        # group's words: ``api/routes/proposals.py``, ``record/proposals.py``), so that is where its
        # decision in force is read, and an answer written from the question names it too.
        answered_at = str(row.get("answers_at") or row["target"])
        found = in_force.get(answered_at) or answered.get(row["target"])
        if found is not None and again is not None and not again.holds(str(row["target"]), found):
            found = None  # a proposal with other members behind the same text is a new question
        if found is None and again is not None:
            found = again.find(answered_at) or again.find(str(row["target"]))
        # A row can be answered where else its answer is kept: a word question is also
        # answered on the word's own target, ``term:<word>``, from the word's page.
        for elsewhere in row.get("answered_on") or ():
            found = found or in_force.get(str(elsewhere))
        row["in_force"] = _in_force(found)
        row["notes"] = _notes(noted.get(answered_at, ()))
        # A set asked once for several rules is answered once every rule's is.
        also = [str(other["target"]) for other in row.get("also") or ()]
        if also and not all(target in in_force for target in also):
            row["in_force"] = None


def _set_changed(
    rows: list[dict[str, Any]], project: Path, events: Sequence[RecordEvent] | None = None
) -> None:
    """A set answered about other files than it holds now waits again.

    An answer on a set is about the files the person was shown (``members`` in its payload). A
    file that joined the set since is asked about, and the earlier answer stays in the Record. An
    answer whose payload names no files stands. The function removes each row's ``member_ids``
    once it has compared them.
    It applies only to a set of copies: any other group takes in a new member unasked. The row
    says what was answered, and about how many files (``answered_before``).
    """
    held = latest_by_target(read_events(project) if events is None else events)
    for row in rows:
        now = set(row.pop("member_ids", ()) or ())
        copies = any(word.get("word") == "copies" for word in row.get("words") or ())
        if not row.get("in_force") or not copies:
            continue
        for target in [row["target"], *(other["target"] for other in row.get("also") or ())]:
            event = held.get(str(target))
            said = json.loads(event.payload or "{}") if event is not None else {}
            shown = said.get("members") if isinstance(said, dict) else None
            if isinstance(shown, list) and set(shown) != now:
                row["answered_before"] = {"label": row["in_force"]["label"], "files": len(shown)}
                row["in_force"] = None
                break


def _carried_answers(
    catalog: Catalog,
    rows: list[dict[str, Any]],
    project: Path,
    events: Sequence[RecordEvent] | None = None,
) -> None:
    """A pair confirmed under another name shows that answer.

    A pair that a person said is made from the other, either way round, stays published when its
    rule is declared again and the proposal takes a new name
    (``record.answers.unanswered_pairs``). On the evidence answered, its row reads the answer
    where it was given (``answered_on``), so the pair is neither asked again nor lost.
    """
    events = read_events(project) if events is None else events
    answers = answers_read(events)
    if not answers.pairs:
        return
    held = latest_by_target(events)
    waiting = [
        row
        for row in rows
        if str(row["target"]) not in held
        and frozenset((str(row["source_id"]), str(row["target_id"]))) in answers.pairs
    ]
    if not waiting:
        return
    carried = carried_confirmations(
        [
            (
                str(row["candidate_id"]),
                str(row["source_id"]),
                str(row["target_id"]),
                str(row["rule_id"]),
            )
            for row in waiting
        ],
        answers,
        stored_evidence(catalog, [str(row["candidate_id"]) for row in waiting]),
    )
    for row in waiting:
        named = carried.get(str(row["candidate_id"]))
        if named:
            row["answered_on"] = list(named)


def _asked_again(
    catalog: Catalog,
    rows: list[dict[str, Any]],
    project: Path,
    events: Sequence[RecordEvent] | None = None,
) -> None:
    """A pair answered on evidence it no longer rests on waits again.

    The pair is proposed again only where what proposed it changed (``record/answers.py``). The
    answer stores the pair's evidence, and a pair whose evidence now differs is asked again, with
    its earlier answer still in the Record. An answer that stores no evidence stands.
    """
    held = latest_by_target(read_events(project) if events is None else events)
    stored: dict[str, dict[str, Any]] = {}
    for row in rows:
        event = held.get(str(row["target"])) if row.get("in_force") else None
        said = json.loads(event.payload or "{}") if event is not None else {}
        if isinstance(said, dict) and isinstance(said.get("evidence"), dict):
            stored[str(row["candidate_id"])] = said["evidence"]
    if not stored:
        return
    now = stored_evidence(catalog, stored)
    for row in rows:
        candidate = str(row.get("candidate_id"))
        if candidate in stored and not evidence_matches(stored[candidate], now.get(candidate, {})):
            row["in_force"] = None


def _progress(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How far a review has got: answered, and what remains."""
    answered = [row for row in rows if row.get("in_force")]
    return {
        "rows": len(rows),
        "answered": len(answered),
        "remaining": len(rows) - len(answered),
    }


def _tally(values: Iterable[str]) -> list[dict[str, Any]]:
    counted = Counter(values)
    return [
        {"value": value, "n": count}
        for value, count in sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    ]


def page(
    catalog: Catalog,
    project: Path,
    kind: str,
    *,
    top: str | None = None,
    rule: str | None = None,
    order: Sequence[str] | None = None,
    limit: int = LIMIT,
    offset: int = 0,
    answered: bool = False,
    waiting_only: bool = False,
    events: Sequence[RecordEvent] | None = None,
    counting: bool = False,
) -> dict[str, Any]:
    """Return one review page: its rows filtered and paged, its progress, and its filters.

    ``top`` keeps the rows about a file under one top-level folder, and ``rule`` the rows one rule
    produced. The progress is counted over every row of the kind, and again over the rows the
    filters keep, so a person sees both how far the whole review has got and this slice.
    Answered rows are left out unless ``answered`` asks for them, and ``hidden_answered`` says
    how many of the kept rows that left out. ``waiting_only`` asks for the answered rows that the
    run has not yet read (:func:`answer_waits`). A row stays with its answer until Re-run reads
    it, and then leaves the page, so the page is never a pile of answers already applied.
    ``counting`` is the tabs' counts asking: what a row shows and no count reads may be left
    unwritten.
    """
    if kind not in KINDS:
        message = f"no review page {kind!r}; the pages are {', '.join(KINDS)}"
        raise LookupError(message)
    if not catalog.has_run:
        return {
            "kind": kind,
            "rows": [],
            "progress": _progress([]),
            "shown": _progress([]),
            "hidden_answered": 0,
        }
    events = read_events(project) if events is None else events
    rows, extra = _built(catalog, project, kind, order, events, counting=counting)
    extra = dict(extra)
    if top is not None and "within_experiments" in extra:
        extra["within_experiments"] = [
            held for held in extra["within_experiments"] if top in held["folders"]
        ]
    kept = [
        row
        for row in rows
        if (top is None or top in row.get("folders", ()))
        and (rule is None or rule in _rules_of(row))
    ]
    if answered and waiting_only:
        from ingence.completeness.elsewhere import run_watermark

        watermark = run_watermark(catalog)
        listed = [row for row in kept if answer_waits(row.get("in_force"), watermark)]
    else:
        listed = kept if answered else [row for row in kept if not row.get("in_force")]
    # Information shown beside the rows (the claim samples' per-file groups): filtered the same
    # way, and never among the rows, so it counts toward nothing waiting.
    told = [
        group
        for group in extra.pop("informational", ())
        if (top is None or top in group.get("folders", ()))
        and (rule is None or group.get("rule_id") == rule)
    ]
    if told or kind == "claims":
        extra["informational"] = told
    return {
        "kind": kind,
        "progress": _progress(rows),
        "shown": _progress(kept),
        "answered_shown": answered,
        "hidden_answered": len(kept) - len(listed),
        "filters": {
            "top": top,
            "rule": rule,
            "tops": _tally(folder for row in rows for folder in row.get("folders", ())),
            "rules": _tally(
                [rule_id for row in rows for rule_id in _rules_of(row)]
                + [str(group["rule_id"]) for group in told]
            ),
        },
        # Each row a copy: a caller may add to what it is handed, never to the rows held.
        "rows": [dict(row) for row in listed[offset : offset + limit]],
        "offset": offset,
        # Every row is counted, however many a page shows. The engine puts no cap on what it
        # proposes, and the Dashboard shows 50 at a time. ``listed`` is all the rows that the
        # filters keep, so the page says "51 to 100 of 1,234" and pages through them.
        "listed": len(listed),
        "limit": limit,
        **extra,
    }


def forget_held(project: Path | None = None) -> None:
    """Let go of the rows held for ``project`` (a server that stops serving it), or of every
    project's (a test's fresh start)."""
    with _HELD_GUARD:
        for held in (_HELD, _HELD_BUILT, _HELD_BUILDING):
            for key in [key for key in held if project is None or key[0] == _held_name(project)]:
                held.pop(key, None)


def _held_name(project: Path) -> str:
    return str(project.resolve())


def _hold(key: tuple[str, str]) -> None:
    """Keep the rows of at most ``HELD_PROJECTS`` projects, the latest asked kept (under guard)."""
    tables: tuple[dict[tuple[str, str], Any], ...] = (_HELD, _HELD_BUILT)
    for held in tables:
        if key in held:
            held[key] = held.pop(key)  # the latest asked, last
        projects = list(dict.fromkeys(name for name, _ in held))
        for name in projects[: max(0, len(projects) - HELD_PROJECTS)]:
            for stale in [each for each in held if each[0] == name]:
                held.pop(stale, None)


def _built(
    catalog: Catalog,
    project: Path,
    kind: str,
    order: Sequence[str] | None,
    events: Sequence[RecordEvent],
    *,
    counting: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A page's every row with its answers read, and what comes beside them, before any filter.

    The function holds these for the kinds of ``HELD_KINDS``: the rows as built under the run
    read, and the rows with their answers read under the Record's state too. Neither is changed
    after, so a request filters and pages them as they are. A count builds them without what only
    a page shows (each set's names written once), and the page writes that onto the held rows when
    it is first shown.
    """
    if kind not in HELD_KINDS:
        rows, extra = _build(catalog, project, kind, order)
        _answers_read(catalog, project, kind, rows, extra, events)
        return rows, extra
    key = (_held_name(project), kind)
    run_id = catalog.run_directory.name if catalog.run_directory is not None else ""
    state = (max((event.sequence for event in events), default=0), len(events))
    with _HELD_GUARD:
        building = _HELD_BUILDING.setdefault(key, threading.Lock())
    with building:
        with _HELD_GUARD:
            found = _HELD.get(key)
            made = _HELD_BUILT.get(key)
        if made is None or made[0] != run_id:
            built_rows, built_extra = _build(catalog, project, kind, order, shown=not counting)
            made = (run_id, built_rows, built_extra, not counting)
            found = None
        elif not counting and not made[3]:
            _with_patterns(catalog, made[1], made[2])
            made = (*made[:3], True)
            found = None
        with _HELD_GUARD:
            _HELD_BUILT[key] = made
        if found is not None and found[0] == run_id and found[1] == state:
            with _HELD_GUARD:
                _hold(key)
            return found[2], found[3]
        rows, extra = _copies(made[1], made[2])
        _answers_read(catalog, project, kind, rows, extra, events)
        with _HELD_GUARD:
            _HELD[key] = (run_id, state, rows, extra)
            _hold(key)
        return rows, extra


def _copies(
    rows: Sequence[dict[str, Any]], extra: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows as built, copied so reading answers onto them leaves the built rows as they were.

    Reading answers sets and removes a row's own keys only, so each row is copied once over, and
    so is each group listed under an experiment."""
    copied = dict(extra)
    if "within_experiments" in copied:
        copied["within_experiments"] = [
            {**held, "groups": [dict(group) for group in held["groups"]]}
            for held in copied["within_experiments"]
        ]
    return [dict(row) for row in rows], copied


def _build(
    catalog: Catalog,
    project: Path,
    kind: str,
    order: Sequence[str] | None,
    *,
    shown: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build a page's every row, the whole list, before its answers are read; ``shown`` false
    leaves out what only a page shows (the Groupings page's names written once)."""
    if kind == "classes":
        rows, extra = _class_rows(catalog, project, order, patterns=shown)
        return rows, extra
    builder = {
        "questions": _question_rows,
        "claims": _claim_rows,
        "candidates": _candidate_rows,
        "extensions": _extension_rows,
    }[kind]
    rows, extra = builder(catalog, project, order)
    if kind == "questions":
        # The two proposals that a person accepts or refuses: each row says which it is and the
        # Record target its answer is written on.
        from ingence.api.routes.proposals import folders_asked_about, proposal_of

        # Each question says the version of the rule that raised it, as a class and a claim do.
        versions = {
            str(rule_id): str(version)
            for rule_id, version in catalog.query(
                "SELECT rule_id, max(version) FROM rules GROUP BY rule_id"
            ).fetchall()
        }
        # Every folder a question asks about, read at once rather than one query per question.
        folders = folders_asked_about(catalog, rows)
        for row in rows:
            row.setdefault("rule_version", versions.get(str(row.get("rule_id"))))
            proposed = proposal_of(catalog, row, folders=folders)
            if proposed is not None:
                row["proposal"] = proposed
                row["answers_at"] = proposed["target"]
                # A proposal's own words: accept or refuse, as its page offers.
                row["words"] = proposed["words"]
                if proposed["kind"] == "pack":
                    # A pack's own reason and files, never the finding's for all packs, nor its
                    # one root counted as the files it would read.
                    row["reason"] = proposed.get("reason", row.get("reason"))
                    row["entries_affected"] = int(proposed.get("files", 0)) or row.get(
                        "entries_affected"
                    )
    return rows, extra


def _answers_read(
    catalog: Catalog,
    project: Path,
    kind: str,
    rows: list[dict[str, Any]],
    extra: Mapping[str, Any],
    events: Sequence[RecordEvent],
) -> None:
    """Give a page's rows, and what is listed beside them, the answers the Record holds."""
    told_examples = [
        example for group in extra.get("informational", ()) for example in group["examples"]
    ]
    held_groups = [
        group for held in extra.get("within_experiments", ()) for group in held["groups"]
    ]
    if kind == "candidates":
        _carried_answers(catalog, rows, project, events)
    _decorate(
        [*rows, *told_examples, *held_groups],
        project,
        events,
        words=lambda: WordMap.of_run(catalog, words_as=run_words_as(catalog)),
        members_now=ProposalMembers.now(catalog),
    )
    if kind == "candidates":
        _asked_again(catalog, rows, project, events)
    if kind == "classes":
        _set_changed([*rows, *held_groups], project, events)


def _rules_of(row: Mapping[str, Any]) -> list[str]:
    """Every rule a row is answered for: its own, and each rule that formed the same set."""
    return [
        str(rule_id)
        for rule_id in [row.get("rule_id"), *(other["rule_id"] for other in row.get("also") or ())]
        if rule_id
    ]


def waiting(catalog: Catalog, project: Path) -> dict[str, int]:
    """How many rows wait for an answer on each review page, for the page's tabs.

    If there is nothing to review, the dashboard makes that known clearly. Each figure is the
    page's own ``remaining``, over every row and no filter, so a tab's count is the count that the
    page opens on. Groups told for information count toward nothing.
    """
    events = read_events(project)
    counted = {
        kind: int(
            page(catalog, project, kind, limit=1, events=events, counting=True)["progress"][
                "remaining"
            ]
        )
        for kind in KINDS
    }
    counted["programs"] = int(programs(catalog, project, events=events)["progress"]["remaining"])
    return counted


# ---------------------------------------------------------------------------
# Questions.
# ---------------------------------------------------------------------------


def _predicates(catalog: Catalog) -> dict[str, str]:
    return {
        str(row["rule_id"]): str(row["predicate"] or "")
        for row in _rows(catalog, "SELECT rule_id, predicate FROM rules")
    }


def _question_tops(catalog: Catalog) -> dict[tuple[str, str], list[str]]:
    """The top-level folders each question's files lie in, over every file and not a sample."""
    found: dict[tuple[str, str], list[str]] = {}
    for row in _rows(
        catalog,
        f"""
        WITH asked AS (
            SELECT DISTINCT f.rule_id, u.value AS raised,
                   regexp_replace(f.subject, '^folder:', '') AS entry_id
            FROM findings f, unnest(f.raised) AS u(value)
        )
        SELECT DISTINCT a.rule_id, a.raised, {_TOP_SQL} AS top
        FROM asked a JOIN entries e ON e.entry_id = a.entry_id
        ORDER BY 1, 2, 3
        """,  # noqa: S608 - the interpolation is a constant expression declared above
    ):
        found.setdefault((str(row["rule_id"]), str(row["raised"])), []).append(str(row["top"]))
    return found


def _questions_on_folders(catalog: Catalog) -> set[tuple[str, str]]:
    """The questions every one of whose findings is about a folder: counted as folders."""
    return {
        (str(row["rule_id"]), str(row["raised"]))
        for row in _rows(
            catalog,
            """
            WITH asked AS (
                SELECT f.rule_id, u.value AS raised, f.subject
                FROM findings f, unnest(f.raised) AS u(value)
            )
            SELECT a.rule_id, a.raised
            FROM asked a
            LEFT JOIN entries e ON e.entry_id = regexp_replace(a.subject, '^folder:', '')
            GROUP BY 1, 2
            HAVING bool_and(
                starts_with(a.subject, 'folder:') OR coalesce(e.entry_type = 'directory', false)
            )
            """,
        )
    }


def _word_rows(
    catalog: Catalog, project: Path, rule_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """The word questions of one rule, by word, as rows of the Questions page.

    The rows are built on the page function
    (:func:`~ingence.textword.questions.word_question_page`). Each carries the question's sentence,
    the engine's reading shown as the engine's, every place (the words beneath an installed program
    set aside), where a meaning may apply, and the words it takes. Each row is the question that the
    Record keys the answer by, so answering it answers every place.
    """
    page = word_question_page(catalog, project, rule_id=rule_id, limit=1_000_000)
    rows: dict[str, dict[str, Any]] = {}
    for item in page["questions"]:
        word = str(item["word"])
        tops = sorted(
            {
                TOP_LEVEL if folder in ("", ".") else _top(folder, "directory")
                for folder in item["folders"]
            }
        )
        rows[word] = {
            **item,
            "rule_id": rule_id,
            "raised": word,
            "target": question_target(rule_id, word),
            "word_question": True,
            "about_a_word": True,
            "predicate": WORD_QUESTION_PREDICATE,
            "reason": item["sentence"],
            "entries_affected": item["files"],
            "where_folders": item["folders"],
            "folders": tops or [TOP_LEVEL],
            "words": _words(question_words(WORD_QUESTION_PREDICATE)),
            # Answered from the word's own page on the word's target, too (``_decorate``).
            "answered_on": [str(item["target"])],
            # What it asks, in words, as every question row says it.
            "heading": f'What is "{word}"?',
            "counted": "files",
        }
    told = {
        "waiting": page.get("waiting") if not page.get("asked") else None,
        "not_asked": list(page.get("not_asked") or ()),
    }
    return rows, told


def _question_rows(
    catalog: Catalog, project: Path, order: Sequence[str] | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every question the queue asks, in its salience order, each with the words it takes.

    A word question is asked as one. The questions of the rule that asks about a word that
    nothing binds come from the word-question page, one row per word at the place the queue ranks
    it, and are never listed as ordinary rows as well. The function computes where a meaning may
    apply for every question about a word, by the function that the answer's route reads
    (:func:`~ingence.textword.questions.where_it_stands`). So "only where it stands" is offered
    only on a row where a folder can hold it.
    """
    queue = review(catalog, order=order, limit=1_000_000)
    predicates = _predicates(catalog)
    tops = _question_tops(catalog)
    on_folders = _questions_on_folders(catalog)
    rows: list[dict[str, Any]] = []
    programs_named = 0
    groupings_asked = 0
    word_rules = sorted(
        rule for rule, predicate in predicates.items() if predicate == WORD_QUESTION_PREDICATE
    )
    if not word_rules:
        # A rule in force that the run never evaluated asks nothing yet, and the page says why.
        word_rules = declared_word_rules(project)
    word_rows: dict[tuple[str, str], dict[str, Any]] = {}
    word_notes: list[str] = []
    waiting: list[str] = []
    for rule_id in word_rules:
        by_word, told = _word_rows(catalog, project, rule_id)
        word_rows.update({(rule_id, word): row for word, row in by_word.items()})
        word_notes.extend(told["not_asked"])
        if told["waiting"]:
            waiting.append(str(told["waiting"]))
    # Where each question about a word stands, read once per rule, and only for such rules.
    placed: dict[str, dict[tuple[str, str], tuple[RaisedFolder, ...]]] = {}
    for question in queue["questions"]:
        rule_id, raised = str(question["rule_id"]), str(question["raised"])
        asked_on = page_asking(rule_id, predicates.get(rule_id))
        if asked_on == "Programs":
            # "Is this a copy of Fiji" is asked on the Programs page, one row per program,
            # and never among the data's questions.
            programs_named += 1
            continue
        if asked_on == "Groupings":
            # "Do these experiments belong together" is asked first on the Groupings page, where
            # a group is shown with its experiments, and never here as well.
            groupings_asked += 1
            continue
        if rule_id in word_rules:
            # One row per word, from the word-question page, where the queue ranks it.
            # A word the page does not offer (one standing only in names the operating system
            # writes, say) is counted by the page function's own sentences, never listed.
            word_row = word_rows.pop((rule_id, raised), None)
            if word_row is not None:
                rows.append(word_row)
            continue
        about_a_word = bool(question["senses"])
        row = {
            **question,
            "target": question_target(rule_id, raised),
            "about_a_word": about_a_word,
            # The words go with what the rule tests, not with its name. The page says so beside
            # them and gives an example.
            "predicate": predicates.get(rule_id) or None,
            "words": [] if about_a_word else _words(question_words(predicates.get(rule_id))),
            "folders": tops.get((rule_id, raised), [TOP_LEVEL]),
            # What it asks, in words, from one table: heading, lead, example.
            **question_words_for(
                predicates.get(rule_id),
                rule_id,
                raised,
                int(question.get("entries_affected") or 0),
                folders=(rule_id, raised) in on_folders,
            ),
        }
        if about_a_word:
            row["heading"] = f'What does "{raised}" mean here?'
            if rule_id not in placed:
                placed[rule_id] = raised_folders(catalog, rule_id=rule_id)
            stands = scope_where_it_stands(catalog, rule_id, raised, placed=placed[rule_id])
            row["choices"] = choices_for(stands)
            row["where"] = {"folder": stands.folder, "because": stands.because}
        rows.append(row)
    return rows, {
        "dimensions": queue["dimensions"],
        "order": queue["order"],
        "theme_kinds": queue["theme_kinds"],
        "set_aside": len(queue["set_aside"]),
        "programs_named": programs_named,
        "asked_on_groupings": groupings_asked,
        "word_questions": {
            "waiting": " ".join(waiting) or None,
            "not_asked": word_notes,
        },
    }


#: The predicate that reads a folder as an installed program's, and how a rule naming it is
#: spelled where a run predates predicates in the rules table.
_PROGRAM_PREDICATE = "installed_software_in_scope"


def _is_program_rule(rule_id: str, predicates: Mapping[str, str]) -> bool:
    return predicates.get(rule_id) == _PROGRAM_PREDICATE or "installed_software" in rule_id


def page_asking(rule_id: str, predicate: str | None) -> str:
    """Which page of the Dashboard asks a question a rule raised: ``"Programs"`` for a rule about
    an installed program, ``"Groupings"`` for the rule proposing groups of experiments, else
    ``"Questions"``. The one place this is decided, so a command-line count and the page agree."""
    if _is_program_rule(rule_id, {rule_id: predicate or ""}):
        return "Programs"
    if predicate == EXPERIMENT_GROUPS_PREDICATE:
        return "Groupings"
    return "Questions"


def raised_by_page(catalog: Catalog) -> dict[str, int]:
    """The questions the current run raises, counted by the page that asks each: ``"Questions"``,
    ``"Groupings"`` and ``"Programs"``. A page with none is left out.

    The function reads the queue that the Questions page is built from (:func:`review`), by the
    page's own rule (:func:`page_asking`). A question every one of whose findings an installed
    program's defeater set aside is in the queue's set-aside list and is asked on the Programs
    page, whatever rule raised it.
    """
    queue = review(catalog, limit=1_000_000, sample=0)
    predicates = _predicates(catalog)
    counts: Counter[str] = Counter()
    for question in queue["questions"]:
        rule_id = str(question["rule_id"])
        counts[page_asking(rule_id, predicates.get(rule_id))] += 1
    if queue["set_aside"]:
        counts["Programs"] += len(queue["set_aside"])
    return dict(counts)


# ---------------------------------------------------------------------------
# Classes.
# ---------------------------------------------------------------------------


def _class_rows(
    catalog: Catalog, project: Path, order: Sequence[str] | None, *, patterns: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The Groupings page, top-down: experiments first, then the groups no experiment holds.

    Grouping has levels. A person answers "at the highest level where that group appears", and
    groups distinguish experiments, not conditions within one. So the rows asked are, first, each
    proposed group of experiments (the clustering rule's questions,
    :func:`_experiment_group_rows`), and then every class of two or more that a grouping rule
    formed and that no one experiment holds (members in two experiments, or in none). The page
    does not ask about a class whose members all lie in one experiment. It lists that class under
    the experiment, for information, written as its name pattern with the parts that vary named
    (``api/name_patterns.py``). A person can still answer it by opening it.

    A class of one groups nothing, so the page does not ask about it. A class published as
    conflicting is asked about, because a defeated class is still one that a person was shown. A
    class wholly inside an installed program is left off and counted: its members are the
    program's.
    """
    del project, order
    regions = _regions(catalog)
    predicates = _predicates(catalog)
    classes = _rows(
        catalog,
        f"""
        WITH members AS (
            SELECT c.rule_id, c.rule_version, c.object_reference AS key, c.resolution_status,
                   e.entry_id, {_TOP_SQL} AS top, {_BENEATH_SQL} AS beneath
            FROM claims c JOIN entries e ON e.entry_id = c.subject
            WHERE c.predicate LIKE 'groups.%'
              AND c.resolution_status IN ('resolved', 'examined_conflicting')
              AND c.object_reference IS NOT NULL
        )
        SELECT rule_id, max(rule_version) AS rule_version, key,
               count(DISTINCT entry_id) AS members,
               list_sort(list(DISTINCT top)) AS tops,
               bool_and(beneath) AS inside_a_program,
               bool_or(resolution_status = 'examined_conflicting') AS conflicting
        FROM members
        GROUP BY rule_id, key
        HAVING count(DISTINCT entry_id) > 1
        ORDER BY members DESC, rule_id, key
        """,  # noqa: S608 - the interpolations are constant expressions declared above
        [regions],
    )
    # When a person has answered that a set is copies, that answer stands, and the set is never
    # asked again.
    classes = [
        row for row in classes if predicates.get(str(row["rule_id"])) != ANSWERED_COPIES_PREDICATE
    ]
    kept = [row for row in classes if not row["inside_a_program"]]
    placed = _class_placement(catalog)
    folders = _folder_paths(catalog, [str(row["key"]) for row in kept])
    # A word is shown as its files wrote it: the key stays the parts.
    written = written_forms(catalog)
    rows = [
        {
            "target": class_target(str(row["rule_id"]), str(row["key"])),
            "rule_id": row["rule_id"],
            "rule_version": row["rule_version"],
            "key": row["key"],
            "spelled": written_text(spelled_key(str(row["key"])), written),
            "folder": folders.get(str(row["key"])),
            "members": int(row["members"]),
            "listed": placed[(str(row["rule_id"]), str(row["key"]))].listed
            if (str(row["rule_id"]), str(row["key"])) in placed
            else [],
            "conflicting": bool(row["conflicting"]),
            "folders": list(row["tops"] or []),
            "predicate": predicates.get(str(row["rule_id"])) or None,
            "words": _words(class_words(predicates.get(str(row["rule_id"])))),
            "level": "files",
            # What made its files alike, and one invented example.
            "alike_by": ALIKE_BY.get(predicates.get(str(row["rule_id"])) or ""),
            **{
                key: value
                for key, value in question_words_for(
                    predicates.get(str(row["rule_id"])), str(row["rule_id"]), "", 0
                ).items()
                if key in ("rule_plain", "example")
            },
        }
        for row in kept
    ]
    _said_why(catalog, rows)
    rows = _asked_once(rows, placed)
    experiments = experiment_folders(catalog)
    asked: list[dict[str, Any]] = []
    within: dict[str, list[dict[str, Any]]] = {}
    labels = _replicate_labels(catalog)
    for row in rows:
        held = placed.get((str(row["rule_id"]), str(row["key"])))
        names, paths = (held.names, held.paths) if held is not None else ([], [])
        # Each set's name, written once, is for showing it. The tabs' counts read none of it,
        # and it is most of a large project's build, so a count leaves it to be written when the
        # page is shown (:func:`_with_patterns`), in the same place in the row.
        row.update(pattern=None, dimensions=None, varies=None)
        if patterns:
            _write_pattern(row, held, labels)
        # Read once the answers are, then let go (``_set_changed``).
        row["member_ids"] = sorted(set(held.ids)) if held is not None else []
        holders = {experiments.of(path) for path in paths}
        holder = next(iter(holders)) if len(holders) == 1 else None
        if holder is not None:
            # The folder's path is what the page shows; its entry is what keys it (0.4.0).
            row["experiment"] = experiments.path_of(holder)
            row["experiment_id"] = holder
            within.setdefault(holder, []).append(row)
        elif _a_series_across(
            row,
            [(experiments.of(path), name) for path, name in zip(paths, names, strict=True)],
            labels,
        ):
            # A series is shown inside its experiments and never asked across them, because a
            # group is never made by dates or replicate numbers. It is a name rule's class whose
            # experiments' files are linked only by what they count, listed under each.
            placed_in = sorted(each for each in holders if each is not None)
            row.update(series=True, experiments_spanned=len(placed_in))
            for each in placed_in:
                within.setdefault(each, []).append(row)
        else:
            row["experiments_spanned"] = len(holders - {None})
            asked.append(row)
    proposed = _experiment_group_rows(catalog, experiments, predicates)
    return [*proposed, *asked], {
        "inside_programs": len(classes) - len(kept),
        "experiments": len(experiments.folders),
        # A series listed under each experiment it lies in is one group.
        "held_by_experiments": len({row["target"] for held in within.values() for row in held}),
        "within_experiments": [
            {
                "experiment": experiments.folders[unit]["path"],
                "experiment_id": unit,
                "name": experiments.folders[unit]["name"],
                "kind": experiments.folders[unit]["kind"],
                "folders": [_top(experiments.folders[unit]["path"], "directory")],
                "groups": within[unit],
            }
            for unit in sorted(within, key=lambda unit: (experiments.folders[unit]["path"], unit))
        ],
    }


#: The predicate every name rule groups by: a class it formed is judged by its names.
_NAME_PREDICATE = "same_component_values"


def _write_pattern(row: dict[str, Any], held: _Placed | None, labels: frozenset[str]) -> None:
    """Write a set's names once, each varying part in braces, and what each part is."""
    shaped = name_pattern(
        held.names if held is not None else [],
        folders=len(held.parents) if held is not None else 0,
        replicate_labels=labels,
    )
    row.update(pattern=shaped.pattern, dimensions=list(shaped.dimensions), varies=shaped.said)


def _with_patterns(
    catalog: Catalog, rows: Sequence[dict[str, Any]], extra: Mapping[str, Any]
) -> None:
    """Write the names of every set a count built without them, asked or listed under an
    experiment, as the page's own build writes them."""
    placed = _class_placement(catalog)
    labels = _replicate_labels(catalog)
    listed = [group for held in extra.get("within_experiments", ()) for group in held["groups"]]
    seen: set[int] = set()
    for row in [*rows, *listed]:
        if row.get("level") != "files" or id(row) in seen:
            continue
        seen.add(id(row))
        _write_pattern(row, placed.get((str(row["rule_id"]), str(row["key"]))), labels)


def _a_series_across(
    row: Mapping[str, Any], members: list[tuple[str | None, str]], labels: frozenset[str]
) -> bool:
    """Whether a class a name rule formed lies in two or more experiments as one series counted
    in each (:func:`~ingence.grouping.identifiers.series_across`), the run's replicate labels
    counting."""
    from ingence.grouping.identifiers import series_across

    return row.get("predicate") == _NAME_PREDICATE and series_across(
        members, replicate_labels=labels
    )


def _replicate_labels(catalog: Catalog) -> frozenset[str]:
    """The labels the run's Dictionary bound to the replicate kind in its names (``rep``, ``trial``,
    an indexed atom's label): their numbers count repeats."""
    rows = catalog.query(
        "SELECT DISTINCT c.component_type, lower(c.normalized_value) FROM components c "
        "JOIN dictionary_bindings b ON b.component_id = c.component_id "
        "WHERE b.resolution_status = 'resolved' AND b.theme_kind = 'replicate' "
        "AND c.source_scope = 'name' AND c.component_type IN ('word', 'indexed')"
    ).fetchall()
    found: set[str] = set()
    for kind, value in rows:
        label = str(value) if kind == "word" else str(value).rstrip("0123456789")
        if label:
            found.add(label)
    return frozenset(found)


def _said_why(catalog: Catalog, rows: list[dict[str, Any]]) -> None:
    """Give each class of copies the reason it was formed, as its question's first line.

    The reason is said plainly: ``why`` is the sentence ("These 12 files hold identical bytes"),
    ``alike_by`` the list's shorter phrase, and ``strength`` how strongly the reason says they
    are copies. The strength picks the reason that a set formed by several rules is shown by
    (:func:`_asked_once`).
    """
    compared: dict[str, list[str]] = {}
    for rule_id, stored in catalog.query(
        "SELECT rule_id, parameters_json FROM rules WHERE predicate = 'same_observed_metadata'"
    ).fetchall():
        for spec in json.loads(stored or "[]"):
            if isinstance(spec, dict) and spec.get("name") == "compared_facts":
                try:
                    facts = json.loads(str(spec.get("default") or "[]"))
                except ValueError:
                    facts = []
                compared[str(rule_id)] = [str(fact) for fact in facts]
    for row in rows:
        reason = copies_reason(
            row.get("predicate"), str(row["key"]), compared.get(str(row["rule_id"]), ())
        )
        if reason is not None:
            row.update(
                why=reason.sentence(int(row["members"])),
                alike_by=f"with {reason.shared}",
                strength=reason.rank,
            )


#: Where no reason ranks a row, it ranks after every one that does.
_UNRANKED = 99


def _asked_once(
    rows: list[dict[str, Any]], placed: Mapping[tuple[str, str], _Placed]
) -> list[dict[str, Any]]:
    """One question for one set of files, however many rules formed it.

    Two rules that compare contents two ways (a whole digest and a sampled one) form the same
    class of twelve copies, and the page would ask it twice. Rows whose members are the same files
    and whose offered words are the same become one, in the place of the first. The row whose reason
    is strongest leads it (identical bytes before sampled bytes before agreeing metadata,
    ``strength``). The others ride with it under ``also``, each with its own rule, version and
    target. So an answer is written to every rule's class target, and every reader of class verdicts
    sees it on its own rule. The row is answered once every target is (``_decorate``).
    """
    sets: list[list[dict[str, Any]]] = []
    set_of: dict[tuple[frozenset[str], tuple[str, ...]], list[dict[str, Any]]] = {}
    for row in rows:
        held = placed.get((str(row["rule_id"]), str(row["key"])))
        # By entry and not by path: two roots can hold one relative path.
        members = frozenset(held.ids) if held is not None else frozenset()
        same = (members, tuple(word["word"] for word in row["words"]))
        found = set_of.get(same) if members else None
        if found is None:
            found = []
            sets.append(found)
            if members:
                set_of[same] = found
        found.append(row)
    kept: list[dict[str, Any]] = []
    for formed in sets:
        lead = min(formed, key=lambda row: int(row.get("strength", _UNRANKED)))
        others = [row for row in formed if row is not lead]
        if others:
            lead.setdefault("also", []).extend(
                {
                    "target": row["target"],
                    "rule_id": row["rule_id"],
                    "rule_version": row["rule_version"],
                }
                for row in others
            )
        kept.append(lead)
    return kept


@dataclass
class _Placed:
    """One class's members as the Groupings page reads them: every name and path, where they
    lie, and the first few listed in full."""

    names: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    #: The members' entry identifiers, which say whether two classes hold the same files.
    ids: list[str] = field(default_factory=list)
    parents: set[str] = field(default_factory=set)
    listed: list[dict[str, Any]] = field(default_factory=list)


def _class_placement(catalog: Catalog) -> dict[tuple[str, str], _Placed]:
    """Every grouping class's members, in one read of the run's grouping claims.

    The function reads the claims whole and does not bind a list of keys as a parameter, because
    binding thousands of keys costs more than reading the claims (measured on a stored census).
    """
    held: dict[tuple[str, str], _Placed] = {}
    for row in _rows(
        catalog,
        """
        SELECT DISTINCT c.rule_id, c.object_reference AS key, e.entry_id, e.name,
               e.relative_path, e.size_bytes, coalesce(e.parent_entry_id, '') AS parent
        FROM claims c JOIN entries e ON e.entry_id = c.subject
        WHERE c.predicate LIKE 'groups.%'
          AND c.resolution_status IN ('resolved', 'examined_conflicting')
          AND c.object_reference IS NOT NULL
        ORDER BY 1, 2, e.relative_path
        """,
    ):
        placed = held.setdefault((str(row["rule_id"]), str(row["key"])), _Placed())
        placed.names.append(str(row["name"]))
        placed.paths.append(str(row["relative_path"]))
        placed.ids.append(str(row["entry_id"]))
        placed.parents.add(str(row["parent"]))
        if len(placed.listed) < MEMBERS_SHOWN:
            placed.listed.append(
                {
                    "entry_id": row["entry_id"],
                    "name": row["name"],
                    "path": row["relative_path"],
                    "size_bytes": row["size_bytes"],
                }
            )
    return held


def _experiment_group_rows(
    catalog: Catalog, experiments: Experiments, predicates: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Each proposed group of experiments as a row asked first: its member experiments named.

    The clustering rule raises, on each member experiment's folder, the name of the group it is
    proposed into. So a group's members are the folders that raise one value, and the row is the
    one question that the Review queue asks of that value.
    """
    raised = _rows(
        catalog,
        """
        SELECT f.rule_id, u.value AS raised, f.reason, e.entry_id, e.relative_path, e.name
        FROM findings f, unnest(f.raised) AS u(value), entries e
        WHERE f.rule_id IN (SELECT rule_id FROM rules WHERE predicate = ?)
          AND f.subject = 'folder:' || e.entry_id
        ORDER BY f.rule_id, u.value, e.relative_path
        """,
        [EXPERIMENT_GROUPS_PREDICATE],
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in raised:
        grouped.setdefault((str(row["rule_id"]), str(row["raised"])), []).append(row)
    # A word is shown as its files wrote it. ``raised`` is the group's key and its question's
    # target, so it stays the lattice's words, and only what a page shows is written again. A
    # term is shown as its files wrote it too (``groups.words_as``), but only where the run read
    # terms. A word of a group read as spellings that happens to be a term's name is a spelling,
    # and is never shown as another one.
    written = written_forms(catalog)
    if run_words_as(catalog) == "terms":
        written = {**written, **written_terms(catalog)}
    rows: list[dict[str, Any]] = []
    for (rule_id, value), held in grouped.items():
        members = [
            {
                "path": str(item["relative_path"]),
                "name": str(item["name"]),
                "kind": experiments.folders.get(str(item["entry_id"]), {}).get("kind", "named"),
                "entry_id": str(item["entry_id"]),
            }
            for item in held
        ]
        rows.append(
            {
                "target": question_target(rule_id, value),
                "rule_id": rule_id,
                "raised": value,
                "level": "experiments",
                "spelled": written_text(value.split(" [", 1)[0], written),
                "members": len(members),
                "experiments": members,
                "shared": [
                    written_word(word, written) for word in shared_by(str(held[0]["reason"] or ""))
                ],
                "listed": [],
                "folders": sorted({_top(member["path"], "directory") for member in members}),
                "predicate": predicates.get(rule_id) or EXPERIMENT_GROUPS_PREDICATE,
                "words": _words(question_words(predicates.get(rule_id))),
            }
        )
    rows.sort(key=lambda item: (-len(item["experiments"]), str(item["raised"])))
    return rows


def _folder_paths(catalog: Catalog, keys: Sequence[str]) -> dict[str, str]:
    """For a key formed within one folder (a name identity), that folder's path."""
    within: dict[str, str] = {}
    for key in keys:
        try:
            parsed = json.loads(key)
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("folder"), str):
            within[key] = parsed["folder"]
    if not within:
        return {}
    paths = {
        str(row["entry_id"]): str(row["relative_path"] or ".")
        for row in _rows(
            catalog,
            "SELECT entry_id, relative_path FROM entries "
            "WHERE entry_id IN (SELECT unnest(?::VARCHAR[]))",
            [sorted(set(within.values()))],
        )
    }
    return {key: paths[entry] for key, entry in within.items() if entry in paths}


# ---------------------------------------------------------------------------
# Claims.
# ---------------------------------------------------------------------------


#: A rule's values are one per file, and are not sampled value by value, when it reached at
#: least this many distinct values and ...
PER_FILE_VALUES = 30

#: ... at least this many distinct values per conclusion: on average fewer than two files share
#: a value. Sampling three files per value would then ask about nearly every file, one by one,
#: and the page would hold thousands of rows asking whether each file's earliest credible time
#: was right. These two figures shape a page and decide nothing about a file.
PER_FILE_SHARE = 0.5

#: A part of such a value is the part its files share where it takes at most this many values
#: across the rule's conclusions: few enough to read as groups, like a time's ``source``.
SHARED_PART_VALUES = 12

#: What the concluded claims are, as every claims query here reads them: a classifying rule's
#: resolved conclusions about files outside an installed program. Binds the regions (``?``).
_CONCLUDED_FROM = f"""
    FROM claims c
    JOIN entries e ON e.entry_id = c.subject
    LEFT JOIN entries parent ON parent.entry_id = e.parent_entry_id
    WHERE c.predicate LIKE 'classifies.%' AND c.resolution_status = 'resolved'
      AND NOT {_BENEATH_SQL}
"""


def _claim_rows(
    catalog: Catalog, project: Path, order: Sequence[str] | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A sample of every classifying rule's conclusions: each value's, sized to the value.

    Every value that each rule concluded is in the sample. The sample holds as many of its files
    as :func:`representative_sample` finds it needs to be judged: one from every sizeable part of
    its files, never fewer than :data:`CLAIMS_AT_LEAST` and never more than
    :data:`CLAIMS_AT_MOST`. Within a part, the sample goes round-robin over the folders holding
    it (first by name in each folder, then the second, and so on), so a value concluded in forty
    folders is not judged from one. The order is deterministic, so the sample a person judged is
    the sample the next person sees.

    The exception is a rule whose values are one per file, such as a time where every file has
    its own. Sampling those per value asks about every file, one by one, and a person cannot
    answer thousands of such questions. For example, nobody can say why one time preceded
    another. The function recognizes such a rule by the shape of what it concluded:
    :data:`PER_FILE_VALUES` distinct values or more, at :data:`PER_FILE_SHARE` of its conclusions
    or more. It never uses the rule's name. The function groups the rule's conclusions by the
    parts of their values that the files share (``source`` in ``{"at": ..., "source": ...}``),
    with three examples to a group, and says each group in plain words (``api/plain_words.yaml``).
    A group is information. It is not a row waiting for an answer, and the page does not count it
    among the remaining. An example can still be answered by opening it.
    """
    del order
    regions = _regions(catalog)
    per_file = _per_file_rules(catalog, regions)
    concluded = _rows(
        catalog,
        f"""
        SELECT c.claim_id, c.rule_id, c.rule_version, c.object_reference AS value,
               e.entry_id, e.name, e.relative_path, {_FORM_SQL} AS form,
               coalesce(parent.relative_path, '') AS folder,
               {_TOP_SQL} AS top
        {_CONCLUDED_FROM}
          AND c.rule_id NOT IN (SELECT unnest(?::VARCHAR[]))
        ORDER BY c.rule_id, c.object_reference, c.claim_id
        """,
        [regions, sorted(per_file)],
    )
    by_value: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in concluded:
        by_value.setdefault((str(row["rule_id"]), str(row["value"])), []).append(row)
    rows = [
        {**_claim_row(row, of_value=len(members), sample=sample), "places_of_value": places}
        for _, members in sorted(by_value.items())
        for sample in [representative_sample(members)]
        for places in [len({str(member["folder"]) for member in members})]
        for row in sample.files
    ]
    groups = [
        group
        for rule_id in sorted(per_file)
        for group in _per_file_groups(catalog, regions, rule_id, per_file[rule_id])
    ]
    return rows, {
        "sampling": {
            "at_least": CLAIMS_AT_LEAST,
            "at_most": CLAIMS_AT_MOST,
            "part_share": PART_SHARE,
            "parts_cover": PARTS_COVER,
        },
        "informational": groups,
    }


#: The prefix of a claim samples' group of per-file values: shown, never answered.
VALUE_GROUP_PREFIX = "value-group"

#: The most files one value's list names: every file up to here, and a count past it.
VALUE_FILES_LISTED = 5_000


def claim_value(
    catalog: Catalog, project: Path, *, rule_id: str | None, value: str | None
) -> dict[str, Any]:
    """Every file one rule concluded one value about, by the folder it lies in.

    This is the list that a person answers "correct for all" over. A person sees every file that the
    rule read the same way and can confirm the claim for all of them at once. The files are the
    claims the samples are drawn from (``_CONCLUDED_FROM``, outside installed programs), each with
    its claim's target and the decision in force on it. So a page writes one ordinary claim event
    per file and leaves out a file already answered. Nothing here writes.
    """
    if not rule_id or value is None:
        message = "a value's files are asked for by its rule and its value"
        raise LookupError(message)
    if not catalog.has_run:
        return {"rule_id": rule_id, "value": value, "files": 0, "waiting": 0, "places": []}
    found = _rows(
        catalog,
        f"""
        SELECT c.claim_id, e.name, coalesce(parent.relative_path, '') AS folder
        {_CONCLUDED_FROM} AND c.rule_id = ? AND c.object_reference = ?
        ORDER BY folder, e.name, c.claim_id
        """,
        [_regions(catalog), rule_id, value],
    )
    in_force = latest_by_target(read_events(project))
    places: dict[str, list[dict[str, Any]]] = {}
    waiting = 0
    for row in found[:VALUE_FILES_LISTED]:
        target = claim_target(str(row["claim_id"]))
        decided = _in_force(in_force.get(target))
        waiting += decided is None
        places.setdefault(str(row["folder"]) or ".", []).append(
            {"name": row["name"], "target": target, "in_force": decided}
        )
    return {
        "rule_id": rule_id,
        "value": value,
        "files": len(found),
        "listed": min(len(found), VALUE_FILES_LISTED),
        "waiting": waiting,
        "places": [{"folder": folder, "files": held} for folder, held in places.items()],
    }


#: What form a file takes, as a claim sample's parts read it: its extension, casefolded, or
#: "(folder)" for a folder. Over an ``entries`` row aliased ``e``.
_FORM_SQL = (
    "CASE WHEN e.entry_type = 'directory' THEN '(folder)' ELSE lower(coalesce(e.extension, '')) END"
)


@dataclass(frozen=True, slots=True)
class Sample:
    """The files a value shows, and how its files divided into the parts they were drawn from."""

    files: list[dict[str, Any]]
    parts: int


def _folder_parts(folder: str) -> tuple[str, ...]:
    return tuple(part for part in str(folder or "").split("/") if part)


class _Spread:
    """Members round-robin over their folders: each folder's first by name, then each second.

    The class ranks only as far as a sample reads. A sample shows ten files at most
    (:data:`CLAIMS_AT_MOST`). Ranking every member of every value, sorted twice, would cost most of
    a large project's claims page and of its first tab counts. The class takes the folders in
    order of their paths and puts a folder's members in order of name only when a rank reaches it.
    That gives the members in the order that ranking them all gives.
    """

    def __init__(self, members: Sequence[Mapping[str, Any]]) -> None:
        by_folder: dict[str, list[Mapping[str, Any]]] = {}
        for member in members:
            by_folder.setdefault(str(member["folder"]), []).append(member)
        self._folders = [by_folder[folder] for folder in sorted(by_folder)]
        self._ordered = [False] * len(self._folders)
        self._given: list[Mapping[str, Any]] = []
        self._rank = 0
        self._at = 0
        self._total = len(members)

    def __len__(self) -> int:
        return self._total

    @property
    def read(self) -> int:
        """How many members have been handed out so far."""
        return len(self._given)

    def __getitem__(self, index: int) -> Mapping[str, Any]:
        while len(self._given) <= index < self._total:
            if self._at >= len(self._folders):
                self._rank += 1
                self._at = 0
            held = self._folders[self._at]
            if len(held) > self._rank:
                if not self._ordered[self._at]:
                    held.sort(key=lambda item: (str(item["name"]), str(item["claim_id"])))
                    self._ordered[self._at] = True
                self._given.append(held[self._rank])
            self._at += 1
        return self._given[index]


def representative_sample(
    members: Sequence[Mapping[str, Any]],
    *,
    at_least: int = CLAIMS_AT_LEAST,
    at_most: int = CLAIMS_AT_MOST,
    part_share: float = PART_SHARE,
    parts_cover: float = PARTS_COVER,
    divide_share: float = DIVIDE_SHARE,
) -> Sample:
    """The files a value is shown by: enough of them to stand for all of them, and no more.

    Each member names its ``folder`` (its parent's path), ``form`` (its extension), ``name`` and
    ``claim_id``. The function divides the members into parts: by form, and by the folder branches
    beneath the deepest folder that holds all of them. It then divides any part that still holds
    more than ``divide_share`` of them by the folders beneath it, for as long as it divides. So a
    value spread over the pool is divided by top-level folder, one inside one experiment by that
    experiment's subfolders, and one lying mostly under one folder by that folder's own. A part
    that holds at least ``part_share`` of the members is shown. Further parts are shown, largest
    first, until the parts shown hold ``parts_cover`` of the members. The count is then held
    between ``at_least`` (or every member, where there are fewer) and ``at_most``, and never above
    the number of different names among them. The function takes one file from each part in turn,
    largest part first, and takes a second from each only once every part shown has given one.
    Within a part, it takes files round-robin over the part's folders, passing over a name
    already shown. The result is deterministic, so everyone who judges a value sees one sample.
    """
    if not members:
        return Sample(files=[], parts=0)
    total = len(members)
    pending: dict[tuple[tuple[str, ...], str], list[tuple[Mapping[str, Any], tuple[str, ...]]]]
    pending = {}
    placements = [_folder_parts(str(member["folder"])) for member in members]
    # First by the branches beneath the deepest folder holding every file ...
    shared = 0
    while shared < min(len(placed) for placed in placements) and (
        len({placed[shared] for placed in placements}) == 1
    ):
        shared += 1
    for member, placed in zip(members, placements, strict=True):
        branch = placed[: shared + 1]
        pending.setdefault((branch, str(member.get("form") or "")), []).append((member, placed))
    # ... then any part still holding more than ``divide_share`` by the folders beneath it.
    divided: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    while pending:
        (branch, form), within = pending.popitem()
        depth = len(branch)
        deeper = [item for item in within if len(item[1]) > depth]
        if len(within) <= divide_share * total or not deeper:
            divided[("/".join(branch), form)] = [member for member, _ in within]
            continue
        here = [member for member, placed in within if len(placed) == depth]
        if here:
            # The files lying in the branch's own folder, beside its subfolders: a part.
            divided[("/".join(branch) + "/.", form)] = here
        for item in deeper:
            pending.setdefault((item[1][: depth + 1], form), []).append(item)
    ordered = sorted(divided.items(), key=lambda item: (-len(item[1]), item[0]))
    needed = covered = 0
    for _, part in ordered:
        if len(part) < part_share * total and covered >= parts_cover * total:
            break
        needed += 1
        covered += len(part)
    # A name shown once stands for its copies elsewhere: a value whose files are one name in
    # many folders (a thumbnail cache in every folder) is shown once, not once per folder.
    names = len({str(member["name"]) for member in members})
    target = min(at_most, total, names, max(min(at_least, total), needed))
    queues = [_Spread(part) for _, part in ordered]
    cursors = [0] * len(queues)
    shown: set[str] = set()
    picked: list[dict[str, Any]] = []
    while len(picked) < target:
        gave = False
        for index, queue in enumerate(queues):
            if len(picked) >= target:
                break
            while cursors[index] < len(queue) and str(queue[cursors[index]]["name"]) in shown:
                cursors[index] += 1
            if cursors[index] < len(queue):
                chosen = queue[cursors[index]]
                cursors[index] += 1
                shown.add(str(chosen["name"]))
                picked.append(dict(chosen))
                gave = True
        if not gave:
            break
    return Sample(files=picked, parts=len(ordered))


def _claim_row(
    row: Mapping[str, Any], *, of_value: int, sample: Sample | None = None
) -> dict[str, Any]:
    """One sampled conclusion, as a row of the claims page shows it."""
    return {
        "target": claim_target(str(row["claim_id"])),
        "claim_id": row["claim_id"],
        "rule_id": row["rule_id"],
        "rule_version": row["rule_version"],
        "value": row["value"],
        "of_value": of_value,
        "shown_of_value": len(sample.files) if sample is not None else None,
        "parts_of_value": sample.parts if sample is not None else None,
        "entry_id": row["entry_id"],
        "name": row["name"],
        "path": row["relative_path"],
        "folder": row["folder"] or ".",
        "folders": [row["top"]],
        "words": _words(CLAIM_WORDS),
    }


def _per_file_rules(catalog: Catalog, regions: Sequence[str]) -> dict[str, bool]:
    """The rules whose values are one per file, each saying whether its values are objects."""
    found = _rows(
        catalog,
        f"""
        SELECT c.rule_id, count(*) AS claims, count(DISTINCT c.object_reference) AS values_,
               bool_and(json_valid(c.object_reference) AND starts_with(c.object_reference, '{{'))
                   AS objects
        {_CONCLUDED_FROM}
        GROUP BY c.rule_id
        """,
        [regions],
    )
    return {
        str(row["rule_id"]): bool(row["objects"])
        for row in found
        if int(row["values_"]) >= PER_FILE_VALUES
        and int(row["values_"]) >= PER_FILE_SHARE * int(row["claims"])
    }


def _json_path(part: str) -> str:
    """A JSON path naming one part of an object, quoted so any name reads as itself."""
    return '$."' + part.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _shared_parts(
    catalog: Catalog, regions: Sequence[str], rule_id: str
) -> tuple[list[str], list[str]]:
    """Which parts of a rule's object values its files share, and which differ file by file."""
    parts = [
        str(row["part"])
        for row in _rows(
            catalog,
            f"""
            SELECT DISTINCT unnest(
                CASE WHEN json_valid(c.object_reference) THEN json_keys(c.object_reference) END
            ) AS part
            {_CONCLUDED_FROM} AND c.rule_id = ?
            ORDER BY 1
            """,
            [regions, rule_id],
        )
    ]
    shared: list[str] = []
    differs: list[str] = []
    for part in parts:
        counted = _rows(
            catalog,
            f"""
            SELECT count(DISTINCT CASE WHEN json_valid(c.object_reference)
                THEN json_extract_string(c.object_reference, ?) END) AS n
            {_CONCLUDED_FROM} AND c.rule_id = ?
            """,
            [_json_path(part), regions, rule_id],
        )[0]["n"]
        (shared if int(counted) <= SHARED_PART_VALUES else differs).append(part)
    return shared, differs


def _per_file_groups(
    catalog: Catalog, regions: Sequence[str], rule_id: str, objects: bool
) -> list[dict[str, Any]]:
    """One rule's per-file conclusions, grouped by what their values share, with examples."""
    shared, differs = _shared_parts(catalog, regions, rule_id) if objects else ([], [])
    if shared:
        pairs = ", ".join("?, json_extract_string(c.object_reference, ?)" for _ in shared)
        grouped = f"json_object({pairs})"
        bound: list[Any] = [item for part in shared for item in (part, _json_path(part))]
    else:
        grouped, bound = "'{}'", []
    concluded = f"""
        SELECT c.claim_id, c.rule_id, c.rule_version, c.object_reference AS value,
               {grouped} AS shared,
               e.entry_id, e.name, e.relative_path, {_FORM_SQL} AS form,
               coalesce(parent.relative_path, '') AS folder,
               {_TOP_SQL} AS top
        {_CONCLUDED_FROM} AND c.rule_id = ?
    """
    parameters = [*bound, regions, rule_id]
    sizes = _rows(
        catalog,
        f"""
        WITH concluded AS ({concluded})
        SELECT shared, count(*) AS files, list_sort(list(DISTINCT top)) AS tops
        FROM concluded GROUP BY shared ORDER BY files DESC, shared
        """,  # noqa: S608 - the interpolations are this module's constant expressions
        parameters,
    )
    # Each group's examples are sized to the group as a value's sample is.
    members = _rows(
        catalog,
        f"""
        WITH concluded AS ({concluded})
        SELECT * FROM concluded ORDER BY shared, claim_id
        """,  # noqa: S608 - the interpolations are this module's constant expressions
        parameters,
    )
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in members:
        by_group.setdefault(str(row["shared"]), []).append(row)
    groups = []
    for size in sizes:
        spelled = str(size["shared"])
        held = {str(part): str(value) for part, value in (json.loads(spelled) or {}).items()}
        files = int(size["files"])
        groups.append(
            {
                # A display key only, never a Record target: its own prefix, so it can never be
                # taken for an approved group's ``group:<terms>``.
                "target": f"{VALUE_GROUP_PREFIX}:{rule_id}:{spelled}",
                "rule_id": rule_id,
                "shared": held,
                "differs": differs,
                "files": files,
                "folders": list(size["tops"] or []),
                "explained": value_sentence(held) or _unexplained(held, differs),
                "examples": [
                    {
                        **_claim_row(row, of_value=files, sample=sample),
                        "grouped_by": held,
                        "differing": _parts_of(str(row["value"]), differs),
                    }
                    for sample in [representative_sample(by_group.get(spelled, []))]
                    for row in sample.files
                ],
            }
        )
    return groups


def _parts_of(value: str, parts: Sequence[str]) -> dict[str, Any]:
    """The named parts of one object value, for an example to show what is its own."""
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return {part: parsed.get(part) for part in parts} if isinstance(parsed, dict) else {}


def _unexplained(shared: Mapping[str, str], differs: Sequence[str]) -> str:
    """A group's sentence where the plain words hold none for what its files share."""
    if not shared:
        return (
            "Every file here has a value of its own, so a few are shown rather than asked about "
            "one by one."
        )
    agreed = "; ".join(f"{_spoken(part)} {_spoken(value)}" for part, value in shared.items())
    own = " and ".join(_spoken(part) for part in differs) or "value"
    return (
        f"These files agree on {agreed}. Each has its own {own}, so a few are shown rather than "
        "asked about one by one."
    )


def _spoken(word: str) -> str:
    """A value's part name as a person reads it: underscores as spaces."""
    return str(word).replace("_", " ")


# ---------------------------------------------------------------------------
# Proposed pairs.
# ---------------------------------------------------------------------------


def _candidate_rows(
    catalog: Catalog, project: Path, order: Sequence[str] | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every proposed pair, both ends named with their stages, as the Candidates view shows."""
    from ingence.api.routes.views import candidates

    del project, order
    rows = [
        {
            **row,
            "target": candidate_target(str(row["candidate_id"])),
            "folders": sorted({_top(row["source_path"], "file"), _top(row["target_path"], "file")}),
            "words": _words(CANDIDATE_WORDS),
            # The tier in words, for the line naming the rule.
            "tier_said": TIER_WORDS.get(str(row.get("decision_tier") or "")),
        }
        for row in candidates(catalog, limit=1_000_000)
    ]
    return rows, {}


# ---------------------------------------------------------------------------
# Extensions no file type names.
# ---------------------------------------------------------------------------


def _extension_rows(
    catalog: Catalog, project: Path, order: Sequence[str] | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every extension that no file type names, by files, for the extensions sheet and the Coverage
    view.

    ``opened`` leaves out a file that no dive has opened, as the Coverage view's own count does
    (``coverage.UNREAD``). A file that a person's settings set aside is one of them.
    """
    del order
    store = DictionaryStore.for_project(project)
    held = _rows(
        catalog,
        f"""
        SELECT coalesce(e.compound_extension, '') AS compound,
               lower(coalesce(e.extension, '')) AS extension,
               {_TOP_SQL} AS top,
               count(*) AS files,
               count(*) FILTER (WHERE e.scan_outcome NOT IN
                                ('metadata_only', 'deferred', 'skipped_by_settings'))
                   AS opened
        FROM entries e
        WHERE e.entry_type = 'file' AND NOT e.is_deleted
          AND {filesystem_entry("e")}
        GROUP BY 1, 2, 3
        """,  # noqa: S608 - the interpolation is a constant expression declared above
    )
    typed: dict[str, bool] = {}

    def named(spelling: str) -> bool:
        if spelling not in typed:
            typed[spelling] = bool(spelling) and file_type_scope(store, spelling) is not None
        return typed[spelling]

    unbound: dict[str, dict[str, Any]] = {}
    for row in held:
        if named(str(row["compound"])) or named(str(row["extension"])):
            continue
        spelled = str(row["extension"])
        tally = unbound.setdefault(spelled, {"files": 0, "opened": 0, "folders": set()})
        tally["files"] += int(row["files"])
        tally["opened"] += int(row["opened"])
        tally["folders"].add(str(row["top"]))
    rows = [
        {
            "target": extension_target(spelled),
            "extension": spelled,
            "files": tally["files"],
            "opened": tally["opened"],
            "folders": sorted(tally["folders"]),
            "words": _words(WORTH_READING),
        }
        for spelled, tally in sorted(unbound.items(), key=lambda item: (-item[1]["files"], item[0]))
    ]
    return rows, {}


# ---------------------------------------------------------------------------
# A question about a word, made plain.
# ---------------------------------------------------------------------------


def _spans(
    name: str, word: str, slots: Sequence[Mapping[str, Any]], components: set[str]
) -> tuple[list[list[int]], str]:
    """Where a word stands inside a name, and how that was found.

    The function looks first in the name's own slots (``name_shapes``). The census recorded where
    each component stood, so the span is the one the engine read. Where the Index holds no shape
    or no slot of the component, a case-folded search of the name finds it, and the page says so.
    """
    found = [
        [int(slot["start"]), int(slot["end"])]
        for slot in slots
        if slot.get("component_id") in components and int(slot["end"]) > int(slot["start"])
    ]
    if found:
        return sorted(found), "the name's own slots"
    folded, wanted = name.casefold(), word.casefold()
    at, searched = 0, []
    while wanted and (index := folded.find(wanted, at)) >= 0:
        searched.append([index, index + len(wanted)])
        at = index + len(wanted)
    return searched, "a search of the name"


def word_page(
    catalog: Catalog,
    *,
    word: str | None = None,
    rule_id: str | None = None,
    raised: str | None = None,
    project: Path | None = None,
) -> dict[str, Any]:
    """Return what a person needs to say what a word means: where it is used, and how.

    A question names the word (``rule_id`` and ``raised``: its files are the question's), or the
    word stands alone (the Coverage view's words that bind to nothing: its files are the names
    carrying it). The function returns at most :data:`WORD_SAMPLE` names, chosen round-robin over
    its folders in the order of how many of its files each holds, each with the word's spans. It
    also returns the folders by name and count, and how many files and folders in all. It offers
    no meaning.

    ``computer`` gives what the word's shape says it may be to a computer. This is evidence read
    from the string, the Dictionary's file types (where ``project`` names the project) and the
    names shown, and never a meaning (:func:`computer_shape`).
    """
    if rule_id and raised is not None:
        spelled = raised
        subjects = _rows(
            catalog,
            """
            SELECT DISTINCT regexp_replace(f.subject, '^folder:', '') AS entry_id
            FROM findings f, unnest(f.raised) AS u(value)
            WHERE f.rule_id = ? AND u.value = ?
            """,
            [rule_id, raised],
        )
    elif word:
        spelled = word
        subjects = _rows(
            catalog,
            "SELECT DISTINCT entry_id FROM components WHERE lower(normalized_value) = ?",
            [word.casefold()],
        )
    else:
        message = "a word page names a word, or a question by its rule and what it raised"
        raise LookupError(message)
    identifiers = sorted(str(row["entry_id"]) for row in subjects)
    entries = _rows(
        catalog,
        f"""
        SELECT e.entry_id, e.name, e.relative_path, e.entry_type,
               coalesce(parent.relative_path, '') AS folder, {_TOP_SQL} AS top
        FROM entries e LEFT JOIN entries parent ON parent.entry_id = e.parent_entry_id
        WHERE e.entry_id IN (SELECT unnest(?::VARCHAR[]))
        ORDER BY folder, e.name, e.entry_id
        """,  # noqa: S608 - the interpolation is a constant expression declared above
        [identifiers],
    )
    by_folder: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_folder.setdefault(str(entry["folder"]), []).append(entry)
    ranked = sorted(by_folder, key=lambda folder: (-len(by_folder[folder]), folder))
    sample: list[dict[str, Any]] = []
    depth = 0
    while len(sample) < WORD_SAMPLE and any(depth < len(by_folder[f]) for f in ranked):
        for folder in ranked:
            if depth < len(by_folder[folder]) and len(sample) < WORD_SAMPLE:
                sample.append(by_folder[folder][depth])
        depth += 1
    shown = [str(entry["entry_id"]) for entry in sample]
    components: dict[str, set[str]] = {}
    for row in _rows(
        catalog,
        "SELECT entry_id, component_id FROM components "
        "WHERE entry_id IN (SELECT unnest(?::VARCHAR[])) AND lower(normalized_value) = ?",
        [shown, spelled.casefold()],
    ):
        components.setdefault(str(row["entry_id"]), set()).add(str(row["component_id"]))
    shapes = {
        str(row["entry_id"]): json.loads(row["slots_json"] or "[]")
        for row in _rows(
            catalog,
            "SELECT entry_id, slots_json FROM name_shapes "
            "WHERE entry_id IN (SELECT unnest(?::VARCHAR[])) AND source_scope = 'name'",
            [shown],
        )
    }
    names = []
    for entry in sample:
        identifier = str(entry["entry_id"])
        name = str(entry["name"])
        spans, how = _spans(
            name, spelled, shapes.get(identifier, []), components.get(identifier, set())
        )
        names.append(
            {
                "entry_id": identifier,
                "name": name,
                "path": entry["relative_path"],
                "folder": entry["folder"] or ".",
                "entry_type": entry["entry_type"],
                "spans": spans,
                "found_by": how,
            }
        )
    kinds = {str(entry["entry_type"]) for entry in entries}
    extension_of = None
    if project is not None:
        hit = DictionaryStore.for_project(project).look_up(
            spelled.casefold(), permitted_kinds=EXTENSION_KINDS
        )
        extension_of = hit.sense.canonical if hit is not None else None
    carried = catalog.query(
        "SELECT count(*) FROM entries WHERE entry_type = 'file' "  # noqa: S608 -- a constant
        f"AND {filesystem_entry()} AND lower(extension) = ?",
        [spelled.casefold()],
    ).fetchone()
    shape = computer_shape(
        spelled,
        extension_of=extension_of,
        carried=int(carried[0]) if carried else 0,
        names=names,
    )
    return {
        # A word question asks first whether it is a person's word at all and, where it is
        # not, what it is to a computer. Each reading comes with what the string's shape says for
        # it, and those it fits come first.
        "computer": sorted(
            (
                {**offered, "shape": shape.get(offered["word"])}
                for offered in _words(COMPUTER_READINGS)
            ),
            key=lambda offered: offered["shape"] is None,
        ),
        "cannot_say": _words(["cannot_say"]),
        "word": spelled,
        "rule_id": rule_id,
        "files": len(entries),
        "are": "folders" if kinds == {"directory"} else "files" if kinds <= {"file"} else "items",
        "folder_count": len(by_folder),
        "folders": [
            {"folder": folder or ".", "files": len(by_folder[folder])}
            for folder in ranked[:FOLDERS_SHOWN]
        ],
        "folders_not_shown": max(0, len(ranked) - FOLDERS_SHOWN),
        "tops": _tally(str(entry["top"]) for entry in entries),
        "names": names,
    }


def _stem_length(name: str) -> int:
    """How long a name is before its extension: where a word standing last ends."""
    dot = name.rfind(".")
    return dot if dot > 0 else len(name)


def computer_shape(
    spelled: str,
    *,
    extension_of: str | None,
    carried: int,
    names: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """What a string's shape says it may be to a computer, reading by reading, in plain words.

    The result is evidence and never an answer. The page puts a reading that the shape fits first
    and says why, and a person still chooses, including "a person's word" against every one of
    them. The function reads only the string and the names shown:

    - ``an_extension``: files in this data carry it as their extension (``carried``), named with
      the file type that the Dictionary knows it as where it knows one (``extension_of``). A
      spelling that the Dictionary knows and no file here carries is not enough. Short words are
      often some program's extension somewhere, and a time's "PM" is not a Perl file.
    - ``a_program_mark``: it stands first, or last before the extension, in every name shown, as a
      prefix or suffix that a program adds does, over at least two different names (copies of one
      name are one name).

    The function offers a code and never suggests it, and it does the same for letters that stand
    for nothing. A letter before a digit looks like a code's shape. Measured on the unbound words
    of real laboratory folders, that shape fitted mostly a laboratory's glued shorthand, such as a
    condition and its duration or a reagent and its dye. Those are a person's words. A suggestion
    that is wrong that often teaches a person to ignore the ones that are right.
    """
    found: dict[str, str] = {}
    if carried:
        found["an_extension"] = (
            f"{carried:,} file{'' if carried == 1 else 's'} here end in .{spelled}"
            + (f" ({extension_of})" if extension_of else "")
        )
    placed = [name for name in names if name.get("spans")]
    if len({str(name["name"]) for name in placed}) >= 2:
        if all(int(name["spans"][0][0]) == 0 for name in placed):
            found["a_program_mark"] = f"it stands first in all {len(placed)} names shown"
        elif all(int(name["spans"][-1][1]) == _stem_length(str(name["name"])) for name in placed):
            found["a_program_mark"] = (
                f"it stands last, before the extension, in all {len(placed)} names shown"
            )
    return found


# ---------------------------------------------------------------------------
# Programs, one row each.
# ---------------------------------------------------------------------------

#: The values the program rule raises where it names no program: ``layout folders`` (enough
#: folders are named for a program's layout) and ``program files`` (enough of the folder's files
#: have a program's extension).
_NO_PROGRAM_NAMED = frozenset({"layout folders", "program files"})


#: Letters that begin a word said with a vowel's sound ("an R package", "an MATLAB toolbox").
_VOWEL_SOUNDS = frozenset("AEIOU")
_SPOKEN_LETTERS = frozenset("FHLMNRSX")


def _with_article(name: str) -> str:
    """A kind's name with its article: "a Python package", "an R package"."""
    first = name.partition(" ")[0]
    vowel = first[:1].upper() in _VOWEL_SOUNDS or (
        len(first) == 1 and first.upper() in _SPOKEN_LETTERS
    )
    return f"{'an' if vowel else 'a'} {name}"


def _package_kinds(project: Path) -> dict[str, str]:
    """Each kind of package the Dictionary's layouts declare (not a program), as a page says it.

    A layout's own ``said_as`` where it declares one, otherwise its name with an article.
    """
    try:
        store = DictionaryStore.for_project(project)
    except (OSError, ValueError):
        return {}
    said: dict[str, str] = {}
    for layout in program_layouts(store.sections):
        if not layout.names_a_program:
            said.setdefault(layout.name, layout.said_as or _with_article(layout.name))
    return said


def _plainly(names: Sequence[str], files: int, kinds: Mapping[str, str] | None = None) -> str:
    """A program row's title in plain words: "a copy of Fiji, N files".

    A kind of package is not a program. A folder laid out as a Python package reads "a Python
    package", and only a named program is "a copy of" something. ``kinds`` says how each kind of
    package is said (:func:`_package_kinds`).
    """
    kinds = kinds or {}
    counted = f"{files:,} file{'' if files == 1 else 's'}"
    named = [name for name in names if name not in _NO_PROGRAM_NAMED]
    if not named:
        return f"a folder laid out as an installed program, {counted}"
    if named == ["application bundle"]:
        return f"an application bundle, {counted}"
    programs_named = [name for name in named if name not in kinds]
    kinds_named = [kinds[name] for name in named if name in kinds]
    said = []
    if programs_named:
        said.append(f"a copy of {' and '.join(programs_named)}")
    said.extend(kinds_named)
    return f"{' and '.join(said)}, {counted}"


def programs(
    catalog: Catalog,
    project: Path,
    *,
    answered: bool = False,
    waiting_only: bool = False,
    events: Sequence[RecordEvent] | None = None,
) -> dict[str, Any]:
    """Every installed program as one row: its outermost folder, its size, its parts.

    A program that was answered (a decision in force on its question) leaves the page unless
    ``answered`` asks for it, as on every review page. A program whose layout names no single
    program has nothing to answer and always stays.

    A program is recognized from its top folder, and a rule reads beneath it as the program's. A
    row is the outermost folder that a rule raised. Its parts are the programs nested inside it
    (a bundled runtime) and its own folders, each with its files. The questions raised only inside
    it, which the Review queue sets aside and does not ask, are listed under it here and nowhere
    among the data's questions.
    """
    raised = _program_folders(catalog)
    outermost = _outermost(item["path"] for item in raised)
    kinds = _package_kinds(project) if outermost else {}
    if not outermost:
        return {
            "programs": [],
            "progress": _progress([]),
            "entries_beneath": 0,
            "hidden_answered": 0,
        }
    counts = {
        str(row["region"]): row
        for row in _rows(
            catalog,
            """
            SELECT p.region,
                   count(*) FILTER (WHERE e.entry_type = 'file') AS files,
                   count(*) FILTER (WHERE e.entry_type = 'directory') AS folders,
                   coalesce(sum(e.size_bytes) FILTER (WHERE e.entry_type = 'file'), 0) AS bytes
            FROM (SELECT unnest(?::VARCHAR[]) AS region) p
            JOIN entries e
              ON starts_with(e.relative_path, p.region || '/') AND NOT e.is_deleted
             AND {own}
            GROUP BY p.region
            """.format(own=filesystem_entry("e")),  # noqa: S608 -- a constant condition
            [sorted({item["path"] for item in raised})],
        )
    }
    children = _rows(
        catalog,
        """
        SELECT p.region, c.name, c.relative_path,
               (SELECT count(*) FROM entries k
                 WHERE k.entry_type = 'file' AND NOT k.is_deleted AND {own}
                   AND starts_with(k.relative_path, c.relative_path || '/')) AS files
        FROM (SELECT unnest(?::VARCHAR[]) AS region) p
        JOIN entries parent ON parent.relative_path = p.region AND parent.entry_type = 'directory'
        JOIN entries c ON c.parent_entry_id = parent.entry_id AND c.entry_type = 'directory'
        ORDER BY p.region, files DESC, c.name
        """.format(own=filesystem_entry("k")),  # noqa: S608 -- a constant condition
        [outermost],
    )
    aside = _set_aside_beneath(catalog, outermost)
    predicates = _predicates(catalog)
    rows: list[dict[str, Any]] = []
    for region in outermost:
        own = [item for item in raised if item["path"] == region]
        names = sorted({str(name) for item in own for name in item["raised"]})
        held = counts.get(region, {"files": 0, "folders": 0, "bytes": 0})
        # The question the program's recognition is, "is this a copy of Fiji": one per program
        # named, so a verdict on it covers every copy of that program the run found.
        rule_id = str(own[0]["rule_id"]) if own else ""
        target = question_target(rule_id, names[0]) if own and len(names) == 1 else None
        nested = [
            {
                "path": item["path"][len(region) + 1 :],
                "names": item["raised"],
                "files": int(counts.get(item["path"], {}).get("files", 0)),
                "title": _plainly(
                    [str(name) for name in item["raised"]],
                    int(counts.get(item["path"], {}).get("files", 0)),
                    kinds,
                ),
                "rule_id": item["rule_id"],
            }
            for item in raised
            if item["path"].startswith(f"{region}/")
        ]
        rows.append(
            {
                "path": region,
                "folder": own[0]["folder"] if own else None,
                "names": names,
                "title": _plainly(names, int(held["files"]), kinds),
                "files": int(held["files"]),
                "folders": int(held["folders"]),
                "bytes": int(held["bytes"]),
                "reason": own[0]["reason"] if own else None,
                "rule_id": own[0]["rule_id"] if own else None,
                "programs_inside": nested,
                "parts": [
                    {"name": row["name"], "path": row["relative_path"], "files": int(row["files"])}
                    for row in children
                    if row["region"] == region
                ],
                "set_aside": aside.get(region, []),
                "top": _top(region, "directory"),
                "folders_top": [_top(region, "directory")],
                "target": target,
                "words": _words(question_words(predicates.get(rule_id))) if target else [],
            }
        )
    rows.sort(key=lambda row: (-int(row["files"]), str(row["path"])))
    _decorate([row for row in rows if row["target"]], project, events)
    # A folder that a person said is not a program leaves the page as an answered row does, and
    # stays reachable with the answered rows.
    excluded = folders_not_programs(read_events(project) if events is None else events)
    for row in rows:
        row["not_a_program"] = row["folder"] in excluded
    if answered and waiting_only:
        from ingence.completeness.elsewhere import run_watermark

        watermark = run_watermark(catalog)
        listed = [row for row in rows if answer_waits(row.get("in_force"), watermark)]
    elif answered:
        listed = rows
    else:
        listed = [row for row in rows if not row.get("in_force") and not row["not_a_program"]]
    # A folder a person said is not a program has been answered, whether or not its row asks the
    # program's own question: it counts as answered and never as waiting.
    asked = [row for row in rows if row["target"] and not row["not_a_program"]]
    said_not = sum(1 for row in rows if row["not_a_program"])
    progress = _progress(asked)
    progress = {
        **progress,
        "rows": progress["rows"] + said_not,
        "answered": progress["answered"] + said_not,
    }
    return {
        "programs": listed,
        "progress": progress,
        "entries_beneath": sum(int(row["files"]) + int(row["folders"]) for row in rows),
        "answered_shown": answered,
        "hidden_answered": len(rows) - len(listed),
    }


def _set_aside_beneath(
    catalog: Catalog, outermost: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """The questions the queue sets aside, under the program each one's files lie inside."""
    queue = review(catalog, limit=1, sample=0)
    wanted = {(str(item["rule_id"]), str(item["raised"])): item for item in queue["set_aside"]}
    if not wanted:
        return {}
    placed: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(
        catalog,
        """
        WITH asked AS (
            SELECT DISTINCT f.rule_id, u.value AS raised,
                   regexp_replace(f.subject, '^folder:', '') AS entry_id
            FROM findings f, unnest(f.raised) AS u(value)
        )
        SELECT DISTINCT a.rule_id, a.raised, p.region
        FROM asked a
        JOIN entries e ON e.entry_id = a.entry_id
        JOIN (SELECT unnest(?::VARCHAR[]) AS region) p
          ON e.relative_path = p.region OR starts_with(e.relative_path, p.region || '/')
        ORDER BY p.region, a.rule_id, a.raised
        """,
        [list(outermost)],
    ):
        key = (str(row["rule_id"]), str(row["raised"]))
        question = wanted.get(key)
        if question is None:
            continue
        placed.setdefault(str(row["region"]), []).append(
            {
                "target": question_target(*key),
                "rule_id": key[0],
                "raised": key[1],
                "entries_affected": question["entries_affected"],
                "set_aside_by": question["set_aside_by"],
            }
        )
    return placed
