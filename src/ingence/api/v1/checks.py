"""``GET /api/v1/checks?limit=5``: the few questions worth a person's minute.

The checks are questions ranked by what one answer settles, as ``questions/ranked`` ranks the
sheet and the queue (most files, folders, conclusions or pairs first), cut to a handful. Each
check gives these:

- the reading the run already uses when nobody answers (``default``), or the fact that the engine
  abstains and there is no result until a person answers;
- its options;
- the write that answers it.

The questions are ``questions/ranked``'s, in its order, cut to ``limit`` (1 to 50, 5 unless told).
A limit outside that range is refused in words, as ``jobs`` refuses its limit. Nothing is ranked at
request time. Both routes read the run's stored ranked list, built once by the readouts step after
inference. They read live only the answer log written since. So a question answered leaves the
checks at once and the next one moves up (``questions_ranked.ranked``,
``questions_ranked.with_answers``).

A check's ``default`` is the reading the run already uses when nobody answers. For a class of
conclusions the engine checked, it is the value the engine concluded (``correct`` keeps it). For a
proposed pair or pattern, it is the pairs as proposed. Where the engine abstains until a person
answers (a tied word, a pack, a question it could not decide), ``default`` is null and ``text``
says so. ``answer.body`` carries what the answer route takes (``target``) beside ``question_id``.
The page adds the chosen option's ``value`` as ``word``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.questions_ranked import RankedQuestion, Unit
from ingence.api.v1.shaping import integer, run_required

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["AnswerAction", "CheckDefault", "CheckItem", "CheckOption", "ChecksResponse"]

#: How many checks a request gets where it does not say, and the most it may ask for.
CHECKS_LIMIT = 5
CHECKS_LIMIT_MAX = 50
#: What a check says where the engine abstains until a person answers.
NO_RESULT_UNTIL_ANSWERED = "No result until answered"
#: Where a check is answered: the Record's one way in.
ANSWER_ROUTE = "/api/review/answer"


class CheckDefault(ApiModel):
    """The reading the run already uses when nobody answers."""

    value: str = Field(description="The option's value the run uses now.")
    text: str = Field(description="That reading in words.")


class CheckOption(ApiModel):
    """One answer a check takes."""

    value: str = Field(description="The value the answer route takes.")
    text: str = Field(description="The answer in words, as its button says it.")


class AnswerAction(ApiModel):
    """The write that answers a check: the route and the body to post, less the option chosen."""

    route: str = Field(description="The route to post to: the answer route.")
    body: dict[str, str] = Field(
        description="The body to post, naming the question; the page adds the option's value."
    )


class CheckItem(ApiModel):
    """One check: a question, what it settles, its default and its options."""

    id: Identifier = Field(description="The check's identifier.")
    question_id: Identifier = Field(description="The question it asks, as the sheet names it.")
    kind: str = Field(description="What kind of question it is (a file type, an area's kind).")
    title: str = Field(description="The question in a few words.")
    text: str = Field(
        description="The question in full, or that there is no result until answered."
    )
    default: CheckDefault | None = Field(
        description="The reading the run uses when nobody answers; null where the engine abstains."
    )
    settles_n: Count = Field(
        description="How many files, folders, conclusions or pairs one answer settles."
    )
    settles_unit: Unit = Field(description="What settles_n counts.")
    options: list[CheckOption] = Field(description="The answers it takes.")
    answer: AnswerAction = Field(description="How to answer it.")


@register("checks")
class ChecksResponse(ApiModel):
    """``GET /api/v1/checks``: the checks, ranked by what one answer settles."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier = Field(description="The run answered.")
    total: Count = Field(description="How many checks there are in all, before the limit.")
    checks: list[CheckItem] = Field(description="The checks, what one answer settles most first.")


#: The answer word that keeps a checked conclusion or a proposed pair as the run reads it.
KEEP_WORD = "correct"


def _words(value: str | None) -> str:
    return (value or "").replace("_", " ")


def _default(question: RankedQuestion) -> CheckDefault | None:
    """The reading the run uses when nobody answers, or None where the engine abstains: a
    checked conclusion or a proposed pair keeps its reading only where the keep word is among
    the question's options; otherwise nothing is read until a person answers."""
    words = {button.word for button in question.answers if button.word}
    if KEEP_WORD not in words:
        return None
    if question.section == "checked":
        said = f"Read as {_words(question.value)}" if question.value else "Read as concluded"
        return CheckDefault(value=KEEP_WORD, text=said)
    if question.section == "proposed":
        unit = "pairs" if question.settles_unit == "pairs" else question.settles_unit
        return CheckDefault(value=KEEP_WORD, text=f"The {unit} stand as proposed")
    return None


def _body(question: RankedQuestion) -> dict[str, str]:
    """What the answer route takes: the target, with ``question_id`` kept beside it for the
    page. The page adds the chosen option's ``value`` as ``word``: the answer route takes the
    answer word there, never the question's own word."""
    body = {"question_id": question.key}
    if question.target:
        body["target"] = question.target
    return body


#: How many example files a check's text names.
EXAMPLES_IN_TEXT = 2
#: The kinds whose ranked title is already plain words.
_PLAIN_TITLE_KINDS = frozenset({"pack", "word", "audit", "derivation pattern", "atypical pair"})


#: What a rule's name looks like: words joined by underscores, and nothing else.
_RULE_NAME = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)+$")


def _plain_rule_sentence(rule_id: str | None) -> str:
    """The rule's plain sentence without its identifier handle, or an empty string."""
    from ingence.api.plain_words import rule_sentence

    plain = rule_sentence(rule_id) if rule_id else None
    return re.sub(r"\s*\([a-z0-9_]+\)\.?$", ".", plain).strip() if plain else ""


def _title(question: RankedQuestion) -> str:
    """The question in a few plain words, never a rule's name: a kind's own title where it has
    one, else the question's heading (``question_words``), else the rule's plain sentence."""
    if question.kind in _PLAIN_TITLE_KINDS:
        return question.title
    heading = (question.question or "").strip()
    if heading and not _RULE_NAME.match(heading):
        return heading
    return _plain_rule_sentence(question.rule_id) or _words(heading) or question.title


def _text(question: RankedQuestion) -> str:
    """The question's own sentence, with its subject (the word, folder or file it is about) and
    up to two examples where the ranked question carries them. It is sent whether or not the
    engine has a guess: ``default`` null says there is none."""
    said = (question.question or "").strip() or _plain_rule_sentence(question.rule_id)
    subject = _words(question.value).strip()
    if subject and subject not in said:
        said = f'{said} (about "{subject}")' if said else f'About "{subject}"'
    # File names only, never folders: a check's text reaches the brief at the names level.
    names = [e.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] for e in question.examples]
    examples = [name for name in names if name.strip(" .")][:EXAMPLES_IN_TEXT]
    if examples:
        stop = " " if said.endswith((".", "?", "!")) else ". "
        said = f"{said}{stop}For example: {', '.join(examples)}"
    return said or _title(question)


def _check(question: RankedQuestion) -> CheckItem:
    default = _default(question)
    return CheckItem(
        id=f"check:{question.key}",
        question_id=question.key,
        kind=question.kind,
        title=_title(question),
        text=_text(question),
        default=default,
        settles_n=question.settles_n,
        settles_unit=question.settles_unit,
        options=[
            CheckOption(value=button.word, text=button.label)
            for button in question.answers
            if button.word
        ],
        answer=AnswerAction(route=ANSWER_ROUTE, body=_body(question)),
    )


@route("checks", model=ChecksResponse)
def checks(context: V1Context, query: Mapping[str, Sequence[str]]) -> ChecksResponse:
    """The pinned run's checks: the stored ranked questions, most settled first, cut to
    ``limit``, with the answers given since read over them."""
    from ingence.api.v1.questions_ranked import ranked

    limit = integer(query, "limit", default=CHECKS_LIMIT, low=1, high=CHECKS_LIMIT_MAX)
    run_id = run_required(context)
    order = ranked(context, {"limit": [str(limit)]})
    return ChecksResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        total=order.total,
        checks=[_check(question) for question in order.questions],
    )
