"""``GET /api/v1/questions/ranked``: every question, ranked and grouped.

The Questions tab asks from two lists that overlap: the question sheet (a class of conclusions, a
vocabulary pack, a word between senses, a pattern of derivations) and the Questions page's queue
(what a rule could not decide). This route merges them, one entry per question. The sheet and the
queue name one Record target for the same question, so a question on both lists is one entry. For
each question the route serves a ``rank``, a ``group``, and a ``reason`` in plain words. A view can
then show the conclusions the engine reached first, as correct until a person says otherwise, then
the few questions that lead, with the rest one click away.

Nothing is hidden and every question stays answerable. The route serves every question of both
lists, the whole sheet and not its first ``sheet_budget``. The sheet is built here at the settings'
maximum (:data:`~ingence.api.v1.question_sheet.SHEET_MAXIMUM`) and cut by nothing. So a class of
conclusions or a pattern of derivations that the Sheet page's budget leaves off is a question here
too, with ``on`` saying which page answers it. ``summary.sheet_left_out`` says how many even that
maximum could not hold. ``limit`` and ``offset`` page the questions. ``total`` says how many there
are and ``left_out`` how many lie after the page. Each question carries the Record target that an
answer is written on.

The questions that the Dashboard asks on other pages (groupings, conclusion samples, proposed
pairs, extensions, programs) are counted in ``summary.elsewhere`` and not ranked here. An answered
question is left out unless ``answered=show`` asks for it. The value ``answered=waiting`` asks only
for the answers the run has not read, which leave once Re-run has. ``summary.answered_left_out``
says how many that left out. A question is answered where the queue holds an answer in force, or
where its sheet row carries an ``answer`` (a sheet that keeps an answered question until the next
run). A sheet that drops an answered class at once leaves it to the Record, so the route does not
serve it.

The rank is computed only from what the engine already holds, in the order below, and no number is
made up for this view. Questions are sorted by this tuple, smallest first. There is no score, and
each member of the tuple is shown beside the question:

0. A question about what a word means comes first, whatever follows in the tuple, so that the
   first line of questions is the words. Every other question comes after.
1. ``lead_tier``: 0 where the question's rule declares that its questions lead (``asked_first``
   on the rule, which the queue already reads) or the question is a vocabulary pack (a pack
   loaded changes the words every later question is asked in, as the sheet's own order has it);
   1 where an answer would let the engine conclude something it could not (``conclusions_blocked``
   is above 0) or touches a group (``classes_touched`` is above 0); 2 for every other question.
   ``leads`` is true for tiers 0 and 1.
2. ``section``: a question about what the engine could not decide (``undecided``: a pack, a word,
   whatever a rule raised) before a proposal it made and waits on (``proposed``: a pattern of
   derivations, an atypical pair), before a check of a conclusion it reached and assumes right
   (``checked``: one class of conclusions). The engine's conclusions are taken as right until a
   person says otherwise, so a check ranks last.
3. ``settles_n`` (larger first): how many files, conclusions or pairs one answer settles. This is
   the queue's own first dimension (``entries_affected``) and the sheet's own order for what is
   left after its first questions.
4. ``conclusions_blocked`` (larger first), then the question's place in the whole sheet or in the
   queue, then its key.

The same run gives the same order. A request cannot reorder: ``group`` and ``leads`` filter, and a
view that wants another order has every field to sort by.

Each question carries what a view needs to answer it without another request:

- the version of the rule that asks it (``rule_version``);
- the address of the page that answers it (``address``: ``?question=<key>#review`` for a question
  the Questions page answers, ``#candidates`` for a pattern whose pairs are answered one by one);
- the buttons its kind takes (``answers``, the last of them "Can't say", written as a note where
  the kind has no such verdict);
- up to three example files relative to their root and how many files one answer settles
  (``examples``, ``examples_of``);
- the meanings of a tied word (``senses``);
- the top-level folders its files lie in (``tops``);
- the answer a person gave it since the run (``answer``), which stays with it until Re-run reads
  it.

``rest`` keeps the questions that do not lead, ``key`` names one question (a link to it), and
``title``, ``top`` and ``rule`` narrow what a group lists. ``filters`` counts what each would keep.
A page sends one answer for several questions through the answer route's ``targets``
(``POST /api/review/answer``): one word, one note, one Record event per target.

The route reads a stored list and never ranks at request time. Ranking every question of a large
run takes minutes, so the readouts step stores the whole ranked list once after inference
(:func:`ranked_readout`). This route, and ``checks`` through it, serve pages of that list. The
answer log written since is read live (:func:`with_answers`), so a question answered leaves the
list at once, without a rebuild.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.question_sheet import SheetSense, sheet_page_numbers, whole_sheet
from ingence.api.v1.shaping import boolean, integer, one

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = [
    "RANKED_READOUT",
    "QuestionsRankedResponse",
    "ranked",
    "ranked_readout",
    "with_answers",
]

DEFAULT_LIMIT = 100
LIMITS = (1, 1000)

Kind = Literal["pack", "word", "question", "audit", "derivation pattern", "atypical pair"]
Section = Literal["undecided", "proposed", "checked"]
Unit = Literal["files", "folders", "conclusions", "pairs"]
Where = Literal["sheet", "questions", "claims", "candidates"]

#: The order sections are ranked in, within a lead tier.
SECTION_ORDER: dict[str, int] = {"undecided": 0, "proposed": 1, "checked": 2}
#: Which section a kind of question is in, by what the engine did about it.
_SECTION_OF: dict[str, Section] = {
    "pack": "undecided",
    "word": "undecided",
    "question": "undecided",
    "derivation pattern": "proposed",
    "atypical pair": "proposed",
    "audit": "checked",
}
#: What one answer settles, said by kind where the question does not say it (files, or folders).
_UNIT_OF: dict[str, Unit] = {
    "audit": "conclusions",
    "derivation pattern": "pairs",
    "atypical pair": "pairs",
}
#: Where a question the Sheet page does not show is answered instead: a class of conclusions on
#: the Conclusion samples page, a pattern's or an atypical pair's pairs on Proposed pairs.
_ELSEWHERE_OF: dict[str, Where] = {
    "audit": "claims",
    "derivation pattern": "candidates",
    "atypical pair": "candidates",
}
#: The order a question's pages are listed in.
_WHERE_ORDER: tuple[Where, ...] = ("sheet", "questions", "claims", "candidates")
#: The pages that ask questions the sheet and the queue do not: the Dashboard's name for each view,
#: and the name on its tab, in the order the tabs stand.
OTHER_PAGES: tuple[tuple[str, str], ...] = (
    ("classes", "Groupings"),
    ("claims", "Conclusion samples"),
    ("candidates", "Proposed pairs"),
    ("extensions", "Extensions"),
    ("programs", "Programs"),
)
#: How the sheet spells a kind, as this route does.
_SHEET_KIND: dict[str, Kind] = {
    "pack": "pack",
    "tied word": "word",
    "audit": "audit",
    "derivation pattern": "derivation pattern",
    "atypical pair": "atypical pair",
}


# ---------------------------------------------------------------------------
# The response models.
# ---------------------------------------------------------------------------


class AnswerButton(ApiModel):
    """One answer a question takes, as its button says it."""

    #: The word the Record takes. None for "Can't say" on a kind that has no such verdict (a pack,
    #: a miscellaneous folder): the person's words are written as a note, which decides nothing.
    word: str | None
    label: str
    #: What the answer means, for the button's hover and never its only label.
    title: str | None
    #: For a tied word's meaning: the kind of thing it is.
    theme_kind: str | None


class AnswerGiven(ApiModel):
    """The answer a person gave a question since the run, kept on it until **Re-run** reads it."""

    word: str | None
    #: The label of the button that said it ("Can't say" for a note written as one).
    label: str
    actor: str
    occurred_at: str | None
    note: str | None
    #: Its place in the Record.
    sequence: Count | None
    waits_for_rerun: bool


class RankedQuestion(ApiModel):
    #: Its place in the order served, from 1, over every question and not the page.
    rank: Count
    #: The Record target an answer is written on, else the pattern's own spelling.
    key: Identifier
    kind: Kind
    section: Section
    #: ``rule:<rule id>``, or ``kind:<kind>`` where no rule is named: the questions one rule
    #: raised or one rule's conclusions are checked by.
    group: Identifier
    #: What the question is a question of, in the page's words ("Conclusions to check"): the one
    #: name a chip opens, whatever rules raised the questions under it. A question the queue holds
    #: about one thing is titled by its rule's name in words.
    title: str
    #: The first two members of the rank's tuple, so a view can say why one leads.
    lead_tier: Count
    leads: bool
    #: The question in a sentence.
    question: str
    #: Why it ranks where it does, in plain words: what an answer settles, and what waits on it.
    reason: str
    #: How many files, folders, conclusions or pairs one answer settles.
    settles_n: Count
    settles_unit: Unit
    #: Rule-and-file pairs that could not decide, and rules that abstained, because of it; and
    #: the classes (groups) its files are claimed into. None where the queue does not hold the
    #: question, which is not the same as none: the question was not counted.
    conclusions_blocked: Count | None
    rules_blocked: Count | None
    classes_touched: Count | None
    rule_id: str | None
    #: The Record target an answer is written on; None for a pattern of derivations, whose pairs
    #: are answered one by one.
    target: str | None
    #: The pages that show it, in this order: the Sheet page (within its budget), the Questions
    #: page (what the queue holds), the Conclusion samples page (a class of conclusions the Sheet
    #: page's budget leaves off) and Proposed pairs (a pattern's pairs it leaves off).
    on: list[Where]
    #: Its number on the Sheet page, and its place in the queue: None where it is not on that page.
    sheet_n: Count | None
    queue_position: Count | None
    #: A person has answered it: read from the queue's answer in force, or the answer a sheet row
    #: carries where the sheet keeps an answered question until the next run.
    answered: bool
    #: The version of the rule that asks it, in this run; None where it rests on no rule.
    rule_version: str | None
    #: The page address that answers it: ``?question=<key>#review`` where the Questions page does,
    #: ``#candidates`` for a pattern of derivations, whose pairs are answered one by one.
    address: str
    #: The buttons it takes, in order; empty for a question answered elsewhere (``address``).
    answers: list[AnswerButton]
    #: True where the person may also say what a word means in their own words.
    own_words: bool
    #: Three examples of the files it settles, relative to their root (a pair as ``a -> b``), and
    #: how many of them one answer settles.
    examples: list[str]
    examples_of: Count
    #: For a tied word: its meanings, in the order the Dictionary lists them.
    senses: list[SheetSense] | None
    #: The top-level folders its files lie in: what ``top`` filters by. Empty where unknown.
    tops: list[str]
    #: The rule's own words for why it asked (for a pack: "the clinical pack reads nothing here: no
    #: name binds one of its 18 meanings"). None where only the sheet holds the question.
    asked_because: str | None
    #: What the question is about, as the engine holds it: the word of a tied word, the domain of a
    #: pack, the value a class of conclusions was concluded. None where the kind has none.
    value: str | None
    #: The answer a person gave it since the run, or None. It stays on the question until Re-run
    #: reads it, so a page shows the row with its answer and the question does not move.
    answer: AnswerGiven | None


class QuestionGroup(ApiModel):
    key: Identifier
    #: The rule's name in words, or the kind's.
    label: str
    #: The rule's own sentence where the table of plain words has one.
    meaning: str | None
    questions: Count
    leading: Count
    #: The best rank among its questions.
    first_rank: Count
    kinds: list[Kind]


class QuestionTitle(ApiModel):
    """One chip: every question of one title, with how many lead and how many are answered."""

    title: str
    questions: Count
    leading: Count
    answered: Count
    first_rank: Count


class AskedElsewhere(ApiModel):
    """Questions the Dashboard asks on another page, counted here and not ranked."""

    #: The Dashboard's own name for the page's view (``classes``, ``claims``, ``candidates``,
    #: ``extensions``, ``programs``).
    view: str
    #: The page's name as a person reads it.
    label: str
    #: How many wait for an answer there.
    waiting: Count


class RankedSummary(ApiModel):
    #: Every question served by the order (answered ones only where asked for).
    questions: Count
    leading: Count
    by_section: dict[str, Count]
    by_kind: dict[str, Count]
    answered_left_out: Count
    #: Classes of conclusions and patterns of derivations that the sheet could not hold even at the
    #: settings' maximum (ten thousand questions): 0 below that, said so none is cut unseen.
    sheet_left_out: Count
    #: Questions the Dashboard asks on other pages, not ranked here: groupings, conclusion
    #: samples, proposed pairs, extensions and programs, each page with questions waiting. They
    #: are counted here so none is lost.
    elsewhere: list[AskedElsewhere]
    #: What the rank is computed from, one plain sentence each, in order.
    ranked_by: list[str]
    #: How many of the questions served are answered (served only where ``answered=show``), and how
    #: many of those lead: so a page can say how many remain.
    answered: Count
    leading_answered: Count


class FilterCount(ApiModel):
    value: str
    n: Count


class RankedFilters(ApiModel):
    """What ``top`` and ``rule`` would keep of the questions the group and lead switches name."""

    tops: list[FilterCount]
    rules: list[FilterCount]


@register("questions_ranked")
class QuestionsRankedResponse(ApiModel):
    """``GET /api/v1/questions/ranked``: every question, ranked, grouped and given a reason."""

    schema_version: str
    run_id: Identifier
    questions: list[RankedQuestion]
    groups: list[QuestionGroup]
    #: The titles of every question served, best-ranked first: one chip each.
    titles: list[QuestionTitle]
    summary: RankedSummary
    #: Questions in the order that match the request, and how many lie after this page.
    total: Count
    left_out: Count
    offset: Count
    limit: Count
    group: str | None
    only_leading: bool
    only_rest: bool
    key: str | None
    title: str | None
    top: str | None
    rule: str | None
    filters: RankedFilters


RANKED_BY = [
    "Questions about what a word means come first. Then a question leads when its rule declares "
    "that its questions lead, when it is a vocabulary pack, when an answer would let a "
    "conclusion be reached that could not be, or when it touches a group.",
    "Then what the engine could not decide, before what it proposed, before what it concluded "
    "and assumes right.",
    "Then the more files, conclusions or pairs one answer settles, the earlier.",
    "Then the more conclusions waiting on it, then its place on the sheet or in the queue.",
]


# ---------------------------------------------------------------------------
# Words.
# ---------------------------------------------------------------------------


def _plural(count: int, one_word: str, many: str | None = None) -> str:
    return f"{count:,} {one_word if count == 1 else many or one_word + 's'}"


def _unit_word(unit: str, count: int) -> str:
    return _plural(count, unit.removesuffix("s"), unit)


def _group_words(rule_id: str | None, kind: str) -> tuple[str, str, str | None]:
    """A group's key, its label and its meaning: a rule's, or the kind's where no rule is named."""
    if rule_id:
        from ingence.api.plain_words import rule_sentence

        return f"rule:{rule_id}", rule_id.replace("_", " "), rule_sentence(rule_id)
    return f"kind:{kind}", kind, None


#: What a kind of question is called on a page; a question the queue holds about one thing takes
#: its rule's name in words.
_TITLE_OF_KIND: dict[str, str] = {
    "pack": "Vocabulary packs",
    "word": "Words and their meanings",
    "audit": "Conclusions to check",
    "derivation pattern": "How files were made",
    "atypical pair": "Pairs that follow no pattern",
}


def _title_of(kind: str, label: str) -> str:
    return _TITLE_OF_KIND.get(kind) or (label[:1].upper() + label[1:])


def _reason(
    settles_n: int,
    unit: str,
    *,
    blocked: int | None,
    rules: int | None,
    classes: int | None,
    asked_first: bool,
    kind: str,
) -> str:
    said = [f"settles {_unit_word(unit, settles_n)}"]
    if blocked:
        from_rules = f", from {_plural(rules, 'rule')}," if rules else ""
        said.append(f"{_plural(blocked, 'conclusion')}{from_rules} could not be reached without it")
    if classes:
        said.append(f"{_plural(classes, 'group')} {'waits' if classes == 1 else 'wait'} on it")
    if asked_first and kind != "pack":
        said.append("its rule asks it first")
    if kind == "pack":
        said.append("it changes the words every later question is asked in")
    return "; ".join(said)


# ---------------------------------------------------------------------------
# What a page needs to answer a question.
# ---------------------------------------------------------------------------

#: How many example files a question shows.
EXAMPLES_SHOWN = 3
#: The one verdict that decides nothing, as a button says it.
CANNOT_SAY = "Can't say"
#: The words a vocabulary pack takes, as the buttons say them; "Can't say" is a note.
_PACK_BUTTONS: tuple[tuple[str | None, str, str], ...] = (
    ("accept", "Yes", "load this pack in every later run"),
    ("refuse", "No", "do not load it, and stop proposing it"),
    (None, CANNOT_SAY, "written as a note beside the question; it decides nothing"),
)
#: The words a class of conclusions takes.
_AUDIT_BUTTONS: tuple[tuple[str | None, str, str], ...] = (
    ("correct", "Right", "every conclusion in this class is right"),
    ("wrong", "Wrong", "the conclusions in this class are wrong"),
    ("cannot_say", CANNOT_SAY, "you cannot tell; recorded, it settles nothing"),
)


def _button(
    word: str | None, label: str, title: str | None = None, theme_kind: str | None = None
) -> dict[str, Any]:
    return {"word": word, "label": label, "title": title, "theme_kind": theme_kind}


def _relative_examples(files: Sequence[Any] | None) -> list[str]:
    """Up to three of a question's files, as their paths under the root they lie in."""
    found: list[str] = []
    for item in files or ():
        if not isinstance(item, dict):
            continue
        path = str(item.get("relative_path") or "").strip()
        if path and path not in found:
            found.append(path)
        if len(found) == EXAMPLES_SHOWN:
            break
    return found


def _tops_of(paths: Sequence[str]) -> list[str]:
    """The top-level folders a list of relative paths lie in, in the order first met: a pair is
    ``earlier -> later``, and a file with no folder lies at the top level."""
    from ingence.api.routes.pages import TOP_LEVEL

    tops: list[str] = []
    for path in paths:
        first = path.split(" -> ")[0]
        top = first.split("/")[0] if "/" in first else TOP_LEVEL
        if top not in tops:
            tops.append(top)
    return tops


def _sense_list(senses: Sequence[Any] | None) -> list[dict[str, Any]] | None:
    if not senses:
        return None
    return [
        {"term": str(sense["term"]), "theme_kind": sense.get("theme_kind")}
        for sense in senses
        if isinstance(sense, dict) and sense.get("term")
    ]


def _buttons_for(
    kind: str,
    *,
    has_target: bool,
    words: Sequence[Any],
    senses: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """The buttons a question takes.

    A word that the Record takes is sent as it is stored. "Can't say" is the one verdict that
    decides nothing. It is written as a note where the kind has no such word (a pack, a folder of a
    person's miscellaneous files).
    """
    if not has_target:
        return []
    if kind == "pack":
        return [_button(word, label, title) for word, label, title in _PACK_BUTTONS]
    if kind == "audit":
        return [_button(word, label, title) for word, label, title in _AUDIT_BUTTONS]
    if senses:
        offered = [
            _button(
                str(sense["term"]),
                str(sense["term"]).replace("_", " "),
                "what the word means here",
                None if sense.get("theme_kind") is None else str(sense["theme_kind"]),
            )
            for sense in senses
        ]
        return [*offered, _button("cannot_say", CANNOT_SAY, _AUDIT_BUTTONS[2][2])]
    buttons = [
        _button(
            str(word["word"]),
            CANNOT_SAY if word["word"] == "cannot_say" else str(word.get("label") or word["word"]),
            None if not word.get("explained") else str(word["explained"]),
        )
        for word in words
        if isinstance(word, dict) and word.get("word")
    ]
    if not any(button["word"] == "cannot_say" for button in buttons):
        buttons.append(_button(None, CANNOT_SAY, _PACK_BUTTONS[2][2]))
    return buttons


def _given(
    raw: Any, buttons: Sequence[Mapping[str, Any]], watermark: int | None
) -> dict[str, Any] | None:
    """A person's answer as the page shows it: the label of the button that said it, who, when,
    and whether it waits for Re-run (its Record sequence lies above the run's watermark)."""
    if not isinstance(raw, dict) or not raw:
        return None
    word = None if raw.get("word") is None else str(raw["word"])
    shown = next((b["label"] for b in buttons if word is not None and b["word"] == word), None)
    if word == "cannot_say":
        shown = CANNOT_SAY
    label = str(shown or raw.get("shown") or raw.get("label") or (word or "").replace("_", " "))
    sequence = raw.get("sequence")
    waits = raw.get("waits_for_rerun")
    if waits is None:
        waits = watermark is None or sequence is None or int(sequence) > watermark
    note = raw.get("note")
    return {
        "word": word,
        "label": label or "Answered",
        "actor": str(raw.get("actor") or raw.get("by") or ""),
        "occurred_at": None if not raw.get("occurred_at") else str(raw["occurred_at"]),
        "note": None if not note else str(note),
        "sequence": None if sequence is None else int(sequence),
        "waits_for_rerun": bool(waits),
    }


def _address(key: str, target: str | None) -> str:
    """Where a question is answered: its card on the Questions page, or Proposed pairs."""
    return f"?question={quote(key, safe='')}#review" if target else "#candidates"


# ---------------------------------------------------------------------------
# Merging the two lists.
# ---------------------------------------------------------------------------


def _count(value: Any) -> int | None:
    return None if value is None else int(value)


def _from_queue(position: int, row: Mapping[str, Any]) -> dict[str, Any]:
    proposal = row.get("proposal")
    kind: Kind = (
        "pack"
        if isinstance(proposal, dict) and proposal.get("kind") == "pack"
        else "word"
        if row.get("about_a_word")
        else "question"
    )
    counted = str(row.get("counted") or "files")
    unit: Unit = "folders" if counted == "folders" else "files"
    target = row.get("answers_at") or row.get("target")
    proposal = row.get("proposal")
    words = (
        (proposal.get("words") if isinstance(proposal, dict) else None) or row.get("words") or []
    )
    senses = _sense_list(row.get("senses"))
    return {
        "key": str(target or f"question:{row.get('rule_id')}:{row.get('raised')}"),
        "rule_version": None if row.get("rule_version") is None else str(row["rule_version"]),
        "words": words,
        "senses": senses,
        "own_words": bool(row.get("about_a_word") or row.get("word_question")),
        "asked_because": None if not row.get("reason") else str(row["reason"]),
        "value": str(row["raised"])
        if (row.get("about_a_word") or row.get("word_question"))
        else None,
        "examples": _relative_examples(row.get("subject_files")),
        "tops": [str(top) for top in row.get("folders") or ()],
        "given": row.get("in_force") or None,
        "kind": kind,
        "question": str(row.get("heading") or row.get("raised") or ""),
        "settles_n": int(row.get("entries_affected") or 0),
        "unit": unit,
        "conclusions_blocked": _count(row.get("conclusions_blocked")),
        "rules_blocked": _count(row.get("rules_blocked")),
        "classes_touched": _count(row.get("classes_touched")),
        "asked_first": bool(row.get("asked_first")),
        "rule_id": None if row.get("rule_id") is None else str(row["rule_id"]),
        "target": None if target is None else str(target),
        "queue_position": position,
        "answered": bool(row.get("in_force")),
        # Where the Questions page finds its answer: the target it is answered at, the question an
        # answer names (its own target), and the targets it is also answered on.
        "answer_at": [
            str(row.get("answers_at") or row.get("target")),
            str(row.get("target")),
            *(str(place) for place in row.get("answered_on") or ()),
        ],
    }


def _from_sheet(row: Mapping[str, Any], page_number: int | None) -> dict[str, Any]:
    kind = _SHEET_KIND[str(row["kind"])]
    target = row.get("target")
    examples = [str(example) for example in row.get("examples") or ()][:EXAMPLES_SHOWN]
    return {
        "key": str(target or f"pattern:{row['key']}"),
        "rule_version": None if row.get("rule_version") is None else str(row["rule_version"]),
        "words": [],
        "senses": _sense_list(row.get("senses")),
        "own_words": kind == "word",
        "asked_because": None,
        "value": None if row.get("value") is None else str(row["value"]),
        "examples": examples,
        "tops": [str(top) for top in row.get("tops") or ()] or _tops_of(examples),
        "given": row.get("answer") or None,
        "kind": kind,
        "question": str(row["question"]),
        "settles_n": int(row["settles_n"]),
        "unit": _UNIT_OF.get(kind, "files"),
        "conclusions_blocked": None,
        "rules_blocked": None,
        "classes_touched": None,
        "asked_first": kind == "pack",
        "rule_id": None if row.get("rule_id") is None else str(row["rule_id"]),
        "target": None if target is None else str(target),
        # Its place on the Sheet page, or None where the page's budget leaves it off; and its
        # place in the whole sheet, which orders ties.
        "sheet_n": page_number,
        "sheet_position": int(row["n"]),
        # A question the sheet keeps with its answer until the next run says so on its row.
        "answered": bool(row.get("answer")),
        "answer_at": [] if target is None else [str(target)],
    }


def _merged(
    sheet: Sequence[Mapping[str, Any]],
    page_numbers: Mapping[str, int],
    queue: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One entry per question: a question on both lists keeps the queue's evidence (what it
    blocks, what it touches) and the sheet's count of what it settles.

    ``on`` lists the pages that show it: the Questions page for what the queue holds; the Sheet
    page for what its budget keeps, with its number there; and, for a class of conclusions or a
    pattern of pairs the Sheet page's budget leaves off, the page that answers such a class.
    """
    held: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(queue, start=1):
        entry = _from_queue(position, row)
        entry["sheet_n"] = None
        entry["sheet_position"] = None
        entry["on"] = {"questions"}
        held[entry["key"]] = entry
    for row in sheet:
        entry = _from_sheet(row, page_numbers.get(str(row["key"])))
        entry.setdefault("queue_position", None)
        found = held.get(entry["key"])
        if found is None:
            entry["on"] = set()
            held[entry["key"]] = found = entry
        else:
            found["sheet_n"] = entry["sheet_n"]
            found["sheet_position"] = entry["sheet_position"]
            found["settles_n"] = max(int(found["settles_n"]), int(entry["settles_n"]))
            found["answered"] = bool(found["answered"] or entry["answered"])
            # The sheet's examples are relative to the root already; the queue's sample of files
            # stands where the sheet draws none. Its senses and answer fill what the queue lacks.
            found["examples"] = entry["examples"] or found["examples"]
            found["senses"] = found["senses"] or entry["senses"]
            found["given"] = found["given"] or entry["given"]
            found["own_words"] = bool(found["own_words"] or entry["own_words"])
            found["value"] = found["value"] or entry["value"]
            found["asked_because"] = found["asked_because"] or entry["asked_because"]
            found["tops"] = found["tops"] or entry["tops"]
            if entry["kind"] == "pack":
                found["kind"] = "pack"
                found["asked_first"] = True
        if found["sheet_n"] is not None:
            found["on"].add("sheet")
        elif row["kind"] in ("audit", "derivation pattern", "atypical pair"):
            found["on"].add(_ELSEWHERE_OF[found["kind"]])
    for entry in held.values():
        entry["on"] = [page for page in _WHERE_ORDER if page in entry["on"]]
    return list(held.values())


def _tier(entry: Mapping[str, Any]) -> int:
    if entry["asked_first"] or entry["kind"] == "pack":
        return 0
    if int(entry["conclusions_blocked"] or 0) or int(entry["classes_touched"] or 0):
        return 1
    return 2


def _order_key(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    position = (
        entry["sheet_position"] if entry["sheet_position"] is not None else entry["queue_position"]
    )
    return (
        0 if entry["kind"] == "word" else 1,
        entry["lead_tier"],
        SECTION_ORDER[entry["section"]],
        -int(entry["settles_n"]),
        -int(entry["conclusions_blocked"] or 0),
        1 << 30 if position is None else int(position),
        entry["key"],
    )


#: The name the whole ranked list is stored under among a run's readouts (``ingence.readouts``).
RANKED_READOUT = "questions_ranked"


def _record_sequence(project: Path) -> int:
    """The last sequence in the answer log, withdrawals among them: what a stored list has read."""
    from ingence.record.log import read_events

    return max((int(event.sequence) for event in read_events(project, withdrawn=True)), default=0)


def ranked_readout(project: Path, run_id: str) -> dict[str, Any]:
    """The whole ranked list of one run, as the readouts step stores it.

    The list holds every question of the sheet and the queue, merged, ranked and given its buttons
    and its answer, answered or not, before any filter, limit or offset.

    This is the expensive half of the route (the whole sheet, the queue, every other page's count
    of what waits). So it is built once after inference and kept, and :func:`ranked` serves from it.
    Beside the questions it keeps what serving needs: the run's watermark, the answer log's last
    sequence when it was built (an answer above it is read over the list, :func:`with_answers`),
    the sheet's count of what even its maximum could not hold, the other pages' counts, and each
    group's label and meaning. Each question keeps where its answer is written
    (``answer_at``, the queue's places before the sheet's) and whose shape the answer takes.
    """
    from ingence.api.routes import pages as page_routes
    from ingence.completeness.elsewhere import run_watermark
    from ingence.storage.catalog import open_catalog

    context = V1Context(project=project, run_current=run_id, run_pinned=run_id, job_running=False)
    # Read first: an answer written while the list is built is read over it, never missed.
    record_sequence = _record_sequence(project)
    with open_catalog(project, run_id=run_id) as catalog:
        watermark = run_watermark(catalog)
        sheet = whole_sheet(context, catalog)
        found = page_routes.page(catalog, project, "questions", limit=1_000_000, answered=True)
        waiting = page_routes.waiting(catalog, project)
    elsewhere = [
        {"view": view, "label": label, "waiting": int(waiting[view])}
        for view, label in OTHER_PAGES
        if int(waiting.get(view) or 0)
    ]
    entries = _merged(
        sheet["questions"], sheet_page_numbers(context, sheet["questions"]), found.get("rows") or []
    )
    asked = sheet["summary"]
    # Audits and patterns the sheet could not hold even at the settings' maximum (never, below ten
    # thousand): said, so nothing is cut without a count.
    served_checks = sum(1 for entry in entries if entry["kind"] == "audit")
    served_patterns = sum(1 for entry in entries if entry["kind"] == "derivation pattern")
    sheet_left_out = max(0, int(asked["audit_classes_total"]) - served_checks) + max(
        0, int(asked["patterns_total"]) - served_patterns
    )
    for entry in entries:
        entry["section"] = _SECTION_OF[entry["kind"]]
        entry["lead_tier"] = _tier(entry)
    entries.sort(key=_order_key)
    questions: list[dict[str, Any]] = []
    groups: dict[str, list[str | None]] = {}
    for entry in entries:
        buttons = _buttons_for(
            entry["kind"],
            has_target=entry["target"] is not None,
            words=entry["words"],
            senses=entry["senses"],
        )
        group, label, meaning = _group_words(entry["rule_id"], entry["kind"])
        groups[group] = [label, meaning]
        questions.append(
            {
                "key": entry["key"],
                "kind": entry["kind"],
                "section": entry["section"],
                "group": group,
                "title": _title_of(entry["kind"], label),
                "lead_tier": entry["lead_tier"],
                "leads": entry["lead_tier"] < 2,
                "question": entry["question"],
                "reason": _reason(
                    int(entry["settles_n"]),
                    entry["unit"],
                    blocked=entry["conclusions_blocked"],
                    rules=entry["rules_blocked"],
                    classes=entry["classes_touched"],
                    asked_first=bool(entry["asked_first"]),
                    kind=entry["kind"],
                ),
                "settles_n": int(entry["settles_n"]),
                "settles_unit": entry["unit"],
                "conclusions_blocked": entry["conclusions_blocked"],
                "rules_blocked": entry["rules_blocked"],
                "classes_touched": entry["classes_touched"],
                "rule_id": entry["rule_id"],
                "target": entry["target"],
                "on": entry["on"],
                "sheet_n": entry["sheet_n"],
                "queue_position": entry["queue_position"],
                "answered": bool(entry["answered"]),
                "rule_version": entry["rule_version"],
                "address": _address(entry["key"], entry["target"]),
                "answers": buttons,
                "own_words": bool(entry["own_words"]),
                "examples": list(entry["examples"]),
                "examples_of": int(entry["settles_n"]),
                "senses": entry["senses"],
                "tops": list(entry["tops"]),
                "value": entry["value"],
                "asked_because": entry["asked_because"],
                "answer": _given(entry["given"], buttons, watermark),
                _ANSWER_AT: entry["answer_at"],
                _ANSWER_SHAPE: "queue" if entry["queue_position"] is not None else "sheet",
            }
        )
    return {
        "payload": {
            "run_id": run_id,
            "watermark": watermark,
            "record_sequence": record_sequence,
            "sheet_left_out": sheet_left_out,
            "elsewhere": elsewhere,
            "groups": groups,
            "questions": questions,
        }
    }


#: Where a stored question's answer is written, and the shape its answer takes: kept beside the
#: question in the readout, never served.
_ANSWER_AT = "_answer_at"
_ANSWER_SHAPE = "_answer_shape"


def _said(event: Any) -> dict[str, Any]:
    import json

    try:
        said = json.loads(event.payload) if event.payload else {}
    except (TypeError, ValueError):
        return {}
    return said if isinstance(said, dict) else {}


def with_answers(project: Path, stored: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The stored questions with a person's answers since the list was built read over them.

    The questions keep the stored order. A question answered since is answered, and so leaves the
    list unless asked for. A question whose answer was withdrawn since is asked again. Nothing is
    rebuilt.

    Only the answer log written after the list (``record_sequence``) is read, from the log the
    process already holds (``record.log.read_events``). Where nothing was written since, the
    stored questions are served as they are. A question's answer is found where the Questions
    page finds it, on the targets kept with it (the target it is answered at, the question an
    answer names, the targets it is also answered on), and takes the page's shape or the sheet's.
    """
    from ingence.api.routes import pages as page_routes
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import latest_by_target, read_events
    from ingence.review.question_sheet import _waiting as waiting_answer

    questions: list[dict[str, Any]] = list(stored.get("questions") or ())
    since = int(stored.get("record_sequence") or 0)
    every = read_events(project, withdrawn=True)
    later = [event for event in every if int(event.sequence) > since]
    if not later:
        return questions
    touched: set[str] = set()
    taken_back: set[str] = set()
    for event in later:
        said = _said(event)
        if event.kind is RecordEventKind.WITHDRAW:
            taken_back.update(str(identifier) for identifier in said.get("withdrawn", ()))
            continue
        touched.add(str(event.target))
        if isinstance(said.get("question"), str):
            touched.add(said["question"])
    for event in every:
        if event.event_id in taken_back:
            touched.add(str(event.target))
            said = _said(event)
            if isinstance(said.get("question"), str):
                touched.add(said["question"])
    if not touched:
        return questions
    latest = latest_by_target(read_events(project))
    by_question: dict[str, Any] = {}
    for event in latest.values():
        question = _said(event).get("question")
        if isinstance(question, str):
            by_question[question] = event
    watermark = stored.get("watermark")
    served: list[dict[str, Any]] = []
    for question in questions:
        places = [str(place) for place in question.get(_ANSWER_AT) or ()]
        if not places or touched.isdisjoint(places):
            served.append(question)
            continue
        found = latest.get(places[0]) or (by_question.get(places[1]) if len(places) > 1 else None)
        for elsewhere in places[2:]:
            found = found or latest.get(elsewhere)
        held = page_routes._in_force(found)
        if held is not None and question.get(_ANSWER_SHAPE) == "sheet":
            held = waiting_answer(held)
        given = _given(held, question.get("answers") or [], watermark)
        served.append({**question, "answered": given is not None, "answer": given})
    return served


def ranked(context: V1Context, query: Mapping[str, Sequence[str]]) -> QuestionsRankedResponse:
    """Every question of the sheet and the queue, merged, ranked and grouped (see the module).

    The route serves the run's stored ranked list (:func:`ranked_readout`, built by the readouts
    step after inference, and built once on the first request where a run has none).

    Only the answer log written since the list was built is read live (:func:`with_answers`), so an
    answered question leaves the list at once. Everything else is the run's as the readouts step
    read it: the order, the questions' evidence and counts, the sheet's numbers,
    ``summary.sheet_left_out`` and ``summary.elsewhere`` (the other pages' counts, which move with
    the next run). A change to the questions settings builds the list again on the next request (the
    readout is keyed by them). ``limit``, ``offset``, the switches and the filters are applied to
    the stored list here.
    """
    from ingence.readouts import readout

    limit = integer(query, "limit", default=DEFAULT_LIMIT, low=LIMITS[0], high=LIMITS[1])
    offset = integer(query, "offset", default=0, low=0, high=10_000_000)
    only_group = (one(query, "group") or "").strip() or None
    only_leading = boolean(query, "leads")
    only_rest = boolean(query, "rest")
    if only_leading and only_rest:
        message = "leads and rest name opposite halves of the order: ask for one"
        raise ApiError(message, parameter="rest")
    only_title = (one(query, "title") or "").strip() or None
    only_key = (one(query, "key") or "").strip() or None
    only_top = (one(query, "top") or "").strip() or None
    only_rule = (one(query, "rule") or "").strip() or None
    # ``answered=show`` serves every answered question; ``answered=waiting`` only those whose answer
    # the run has not read yet (they leave once Re-run has, as an answered row always has).
    only_waiting = (one(query, "answered") or "").strip().lower() == "waiting"
    with_answered = only_waiting or boolean(query, "answered", true_word="show")
    run_id = _pinned_run(context)
    stored = readout(context.project, run_id, RANKED_READOUT)["payload"]
    entries = with_answers(context.project, stored)

    def _gone(entry: Mapping[str, Any]) -> bool:
        """Whether a question leaves the list: answered, and not asked to be shown; or, where only
        the answers waiting are asked for, answered and already read by the run."""
        if not entry["answered"]:
            return False
        if not with_answered:
            return True
        given = entry["answer"]
        return only_waiting and given is not None and not given["waits_for_rerun"]

    answered_left_out = sum(1 for entry in entries if _gone(entry))
    listed = [entry for entry in entries if not _gone(entry)]
    labels: Mapping[str, Sequence[str | None]] = stored.get("groups") or {}
    summary = RankedSummary(
        questions=len(listed),
        leading=sum(1 for entry in listed if entry["leads"]),
        by_section=_tally(entry["section"] for entry in listed),
        by_kind=_tally(entry["kind"] for entry in listed),
        answered_left_out=answered_left_out,
        sheet_left_out=int(stored.get("sheet_left_out") or 0),
        elsewhere=[AskedElsewhere(**item) for item in stored.get("elsewhere") or ()],
        ranked_by=RANKED_BY,
        answered=sum(1 for entry in listed if entry["answered"]),
        leading_answered=sum(1 for entry in listed if entry["answered"] and entry["leads"]),
    )
    ranks = [(rank, entry) for rank, entry in enumerate(listed, start=1)]
    named = [
        (rank, entry)
        for rank, entry in ranks
        if (only_group is None or entry["group"] == only_group)
        and (only_title is None or entry["title"] == only_title)
        and (only_key is None or entry["key"] == only_key)
        and (not only_leading or entry["leads"])
        and (not only_rest or not entry["leads"])
    ]
    kept = [
        (rank, entry)
        for rank, entry in named
        if (only_top is None or only_top in entry["tops"])
        and (only_rule is None or entry["rule_id"] == only_rule)
    ]
    filters = RankedFilters(
        tops=_counted(top for _, entry in named for top in entry["tops"]),
        rules=_counted(entry["rule_id"] for _, entry in named if entry["rule_id"]),
    )
    page = kept[offset : offset + limit]
    return QuestionsRankedResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        questions=[_served(rank, entry) for rank, entry in page],
        groups=_groups(ranks, labels),
        titles=_titles(ranks),
        summary=summary,
        total=len(kept),
        left_out=max(0, len(kept) - offset - len(page)),
        offset=offset,
        limit=limit,
        group=only_group,
        only_leading=only_leading,
        only_rest=only_rest,
        key=only_key,
        title=only_title,
        top=only_top,
        rule=only_rule,
        filters=filters,
    )


def _pinned_run(context: V1Context) -> str:
    """The run every read is pinned to, refused as ``pinned_catalog`` refuses where none is."""
    if context.run_pinned is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    return context.run_pinned


def _served(rank: int, entry: Mapping[str, Any]) -> RankedQuestion:
    """One stored question as the route serves it, at its rank, without what is kept beside it."""
    return RankedQuestion.model_validate(
        {
            **{name: value for name, value in entry.items() if not name.startswith("_")},
            "rank": rank,
        }
    )


def _tally(values: Any) -> dict[str, int]:
    counted: dict[str, int] = {}
    for value in values:
        counted[str(value)] = counted.get(str(value), 0) + 1
    return dict(sorted(counted.items()))


def _counted(values: Any) -> list[FilterCount]:
    """What each value of a filter would keep, the most first, then by name."""
    tally = _tally(values)
    return [
        FilterCount(value=value, n=n)
        for value, n in sorted(tally.items(), key=lambda i: (-i[1], i[0]))
    ]


def _titles(ranks: Sequence[tuple[int, Mapping[str, Any]]]) -> list[QuestionTitle]:
    """Every title of the questions served, whatever page is asked for, best-ranked first."""
    held: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for rank, entry in ranks:
        held.setdefault(str(entry["title"]), []).append((rank, entry))
    titles = [
        QuestionTitle(
            title=title,
            questions=len(members),
            leading=sum(1 for _, member in members if member["leads"]),
            answered=sum(1 for _, member in members if member["answered"]),
            first_rank=members[0][0],
        )
        for title, members in held.items()
    ]
    titles.sort(key=lambda found: (found.first_rank, found.title))
    return titles


def _groups(
    ranks: Sequence[tuple[int, Mapping[str, Any]]], labels: Mapping[str, Sequence[str | None]]
) -> list[QuestionGroup]:
    """Every group of the questions served, whatever page is asked for, best-ranked first."""
    held: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for rank, entry in ranks:
        held.setdefault(str(entry["group"]), []).append((rank, entry))
    groups = [
        QuestionGroup(
            key=key,
            label=str(labels[key][0]),
            meaning=labels[key][1],
            questions=len(members),
            leading=sum(1 for _, member in members if member["leads"]),
            first_rank=members[0][0],
            kinds=sorted({member["kind"] for _, member in members}),
        )
        for key, members in held.items()
    ]
    groups.sort(key=lambda group: (group.first_rank, group.key))
    return groups


@route("questions/ranked", model=QuestionsRankedResponse)
def ranked_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> QuestionsRankedResponse:
    return ranked(context, query)
