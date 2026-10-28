"""A person's answers, composed into the Dictionary as senses of their own.

The Dictionary a run consults is the shipped vocabulary plus what a person has said about it. This
module is the second half. Every answer becomes a sense at ``USER`` origin. ``ORIGIN_PRECEDENCE``
ranks ``USER`` at 40, above the 10 at which the core, a pack and a discovered proposal all stand,
so a person's answer is the one thing that wins.

Each answer carries its own scope. So "in this folder that term means this, and everywhere else it
means what it always meant" is expressible: ``SCOPE_PRECEDENCE`` ranks selection, folder, level,
root and project, narrowest first. A section file cannot say that, because
:func:`~ingence.dictionary.store.load_sections` gives one scope to a whole directory. Built from
the Record, each sense keeps the scope its own answer carried.

The senses are derived and never copied. They are built from the Record on every load rather than
written out as YAML beside it. A second copy of a decision is the copy that drifts. The Record is
also one of the four inputs from which a conclusion can be made again (the Index, the Rulebook, the
Dictionary and the Record), so a run's vocabulary is reproducible from the Record without a file
that anybody could edit out from under it.

An assignment and a definition make the same shape. One names a sense that the Dictionary already
holds and the other names a new one. The difference is a fact about what the person was offered, not
about what the sense is. The answer records the difference for a reader. The difference changes
nothing here.

What a person taught travels as a pack section. An answer written in one run project's Record is
a fact about the laboratory's vocabulary, and archiving the project would archive it too.
:func:`pack_section_from_answers` writes the in-force assignments and definitions in the section
format ``dictionary/builtin/`` uses, each with who answered and when. Loaded as a pack, the section
adds vocabulary and decides nothing. A pack sits at ``BUILTIN``'s precedence, so an exported
assignment offers its sense beside the others rather than choosing it. Only a person's answer in a
project's Record chooses. The file says so. Only a project-wide answer is exported. An answer
scoped to a folder names this project's folders, which a pack has none of, so the export counts it
and leaves it out rather than widening it.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any

import yaml

from ingence.core.ids import ScopeKind
from ingence.core.schema import DictionaryOrigin, DictionaryStatus
from ingence.dictionary.pack_manifest import Manifest, with_manifest
from ingence.dictionary.store import Section, Sense
from ingence.record.log import latest_by_target, read_events
from ingence.record.questions import (
    answers_in_force,
    question_target,
    term_target,
    terms_in_force,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ingence.record.questions import QuestionAnswer

__all__ = [
    "ANSWERS_SECTION",
    "ExportedAnswers",
    "answered_sections",
    "pack_section_from_answers",
    "section_from_answers",
]

#: The section a person's answers are gathered into. One section however many answers there
#: are, because a section is a file of senses and these came from one place.
ANSWERS_SECTION = "answers"


def section_from_answers(answers: Sequence[QuestionAnswer]) -> Section | None:
    """Return one section holding a sense per answer, or None where there are none.

    Each answer gets one sense, and senses are never merged. Two answers that name one term at two
    scopes are two senses, because the scope is part of what each says and folding them would
    decide for both folders what a person decided for one. The identity a binding records already
    hashes term, scope and origin, so two such senses are two rows rather than a collision.
    """
    senses = tuple(
        Sense(
            term=answer.term,
            canonical=answer.term,
            theme_kind=answer.theme_kind,
            section=ANSWERS_SECTION,
            surfaces=tuple(dict.fromkeys((answer.raised, *answer.spellings))),
            origin=DictionaryOrigin.USER,
            status=DictionaryStatus.ACTIVE,
            scope=answer.scope,
            properties={"raised_by": answer.rule_id, "defined": answer.defined},
        )
        for answer in answers
    )
    if not senses:
        return None
    return Section(
        name=ANSWERS_SECTION,
        summary="What a person answered about a raised term, read back out of the Record.",
        senses=senses,
        origin=DictionaryOrigin.USER,
    )


def answered_sections(project: Path) -> tuple[Section, ...]:
    """Return the sections a project's own Record adds to the Dictionary, if any.

    Answers to questions and words a person defined where no rule asked are one section, because
    both are a person saying what a spelling means.
    """
    section = section_from_answers((*answers_in_force(project), *terms_in_force(project)))
    return () if section is None else (section,)


class ExportedAnswers:
    """A pack section built from a project's answers, and what was left out of it and why."""

    def __init__(self, text: str, exported: int, left_out: Counter[str], inactive: int = 0) -> None:
        self.text = text
        self.exported = exported
        self.left_out = left_out
        #: How many inactivations the file lists beside its entries.
        self.inactive = inactive


_HEADER = """\
# What people taught one project's Dictionary, exported from its Record.
#
# Each entry is an answer in force: an assignment names a sense the Dictionary already held,
# a definition names a new one, and both carry who answered and when. Loaded as a pack, this
# section adds vocabulary and decides nothing: a pack sits at the shipped vocabulary's
# precedence, so an assignment here offers its sense beside the others rather than choosing it.
# Only a person's answer in a project's Record chooses. Answers scoped to one folder were left
# out, because they name that project's folders.
"""


_INACTIVE_NOTE = """\
#
# The list under "inactive" is what people inactivated in this project's Dictionary. A pack
# adds vocabulary and decides nothing, so loading this file inactivates nothing:
# inactivating is a person's decision, written in a project's Record from the Dictionary
# section of Engine.
"""


def pack_section_from_answers(
    project: Path,
    *,
    section: str,
    inactive: bool = False,
    author: str = "",
    licence: str = "not stated",
) -> ExportedAnswers:
    """Return the project's in-force assignments and definitions as a pack section's YAML.

    One entry per sense, spelled as ``dictionary/builtin/`` spells an entry: the term, its axis
    and the surfaces a person answered about, with the answers behind it recorded beside them.
    Canonically ordered, so exporting an unchanged Record twice writes the same bytes.

    A manifest heads the section (:mod:`~ingence.dictionary.pack_manifest`): the section's name,
    the latest answer's moment as its version, the people who answered as its author unless
    ``author`` names one, ``licence``, the schema, and a digest over its words.

    With ``inactive``, the section lists what people inactivated beside the entries. The list sits
    under a key that the section loader does not read, because a pack cannot inactivate. The list
    is for a person to read, and to repeat in the Dictionary section of Engine if they choose.
    """
    events = latest_by_target(read_events(project))
    left_out: Counter[str] = Counter()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for answer in (*answers_in_force(project), *terms_in_force(project)):
        if answer.scope.kind is not ScopeKind.PROJECT:
            left_out[f"scoped to one {answer.scope.kind.value}"] += 1
            continue
        event = events[
            question_target(answer.rule_id, answer.raised)
            if answer.rule_id
            else term_target(answer.raised)
        ]
        entry = grouped.setdefault(
            (answer.theme_kind, answer.term),
            {
                "term": answer.term,
                "theme_kind": answer.theme_kind,
                "surfaces": set(),
                "answers": [],
            },
        )
        entry["surfaces"].add(answer.raised)
        entry["answers"].append(
            {
                "surface": answer.raised,
                "answered": "define" if answer.defined else "assign",
                "raised_by": answer.rule_id or "nobody: a word defined unasked",
                "actor": event.actor,
                "occurred_at": event.occurred_at,
                "event_id": event.event_id,
            }
        )
    entries = [
        {
            "term": entry["term"],
            "theme_kind": entry["theme_kind"],
            "surfaces": sorted(entry["surfaces"]),
            "answers": sorted(
                entry["answers"], key=lambda item: (item["surface"], item["event_id"])
            ),
        }
        for _, entry in sorted(grouped.items())
    ]
    document: dict[str, Any] = {
        "section": section,
        "origin": DictionaryOrigin.PACK.value,
        "summary": "What people taught a project's Dictionary, from the answers in its Record.",
        "entries": entries,
    }
    listed: list[dict[str, object]] = []
    if inactive:
        from ingence.dictionary.inactive import inactivations_in_force

        listed = inactivations_in_force(project).as_payload()
        if listed:
            document["inactive"] = listed
    # The manifest comes first: named for the section, versioned by the latest answer it holds,
    # written by the people who answered, with a digest over the words. So the file is a pack that
    # ``teach pack install`` checks wherever it travels.
    given = [answer for entry in entries for answer in entry["answers"]]
    manifest = Manifest(
        name=re.sub(r"[^a-z0-9_\-]+", "-", section.casefold()).strip("-_")[:64] or "taught",
        version=max((str(answer["occurred_at"]) for answer in given), default="1"),
        author=author or ", ".join(sorted({str(answer["actor"]) for answer in given})),
        licence=licence,
    )
    header = _HEADER + (_INACTIVE_NOTE if listed else "")
    text = header + yaml.safe_dump(
        with_manifest(document, manifest), sort_keys=False, width=100, allow_unicode=True
    )
    return ExportedAnswers(
        text=text, exported=len(entries), left_out=left_out, inactive=len(listed)
    )
