"""``GET /api/v1/word/meaning?word=``: what a word means here, and a person's correction of it.

The route returns a word's page. It gives what the Dictionary settles the word as now (the winning
sense, its kind, and where it came from: the shipped vocabulary, a pack, or a person), the senses
it ties with or outranked, how many names in the run carry it and in which folders, and the
corrections in force. A correction is a person's own meaning for the word, given in the Record,
and there is one per scope. The page offers the correction from here, from the file inspector and
from search results. The correction itself is written through the one answer route
(``POST /api/review/answer`` with the word's target, ``correcting`` and, to keep it below a
folder, ``below``).

Nothing here writes. The sense is read from the Dictionary the project's Record composes (the
answers are part of it), and the files from the run the request is pinned to.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one, pinned_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.dictionary.store import Sense

__all__ = ["CorrectionItem", "SenseItem", "WordMeaning", "meaning"]

#: The folders a word's page lists for "keep it below a folder", most names first.
FOLDERS_SHOWN = 40


class SenseItem(ApiModel):
    """One meaning the Dictionary holds for the word."""

    term: str
    theme_kind: str
    #: ``builtin`` (the shipped vocabulary), ``pack``, ``discovered`` or ``user`` (a person).
    origin: str
    #: The vocabulary file or pack section it is written in.
    section: str
    #: Where it applies: ``project``, or ``folder:<path>``.
    scope: str
    #: For a person's answer to a question, the rule that asked it. A correction at the same
    #: scope takes that answer's place (``decide.correct_word``).
    raised_by: str | None = None


class CorrectionItem(ApiModel):
    """A person's own meaning for the word, in force in the Record."""

    sequence: Count
    actor: str
    occurred_at: str
    #: ``define`` (a new meaning) or ``assign`` (one the Dictionary already held).
    kind: str
    term: str
    theme_kind: str
    #: The folder it is kept below, or None for the whole project.
    below: str | None
    #: What the Dictionary settled the word as when it was corrected, where it settled it.
    corrected: SenseItem | None


class FolderCount(ApiModel):
    path: str
    files: Count


@register("word_meaning")
class WordMeaning(ApiModel):
    """What one word means in this project, where it stands, and any correction in force."""

    schema_version: str
    run_id: Identifier
    word: str
    #: Names in the run carrying the word, and the folders holding them.
    files: Count
    folders: list[FolderCount]
    folders_not_shown: Count
    #: ``settled`` (one meaning wins), ``contested`` (meanings tie), or ``unbound`` (none).
    standing: str
    in_force: SenseItem | None
    tied: list[SenseItem]
    #: Meanings that matched and lost to the one in force.
    outranked: list[SenseItem]
    #: A person's corrections in force, one per scope (the whole project, or below a folder).
    corrections: list[CorrectionItem]
    #: The kinds of thing a meaning may be, for the correction's choice.
    theme_kinds: list[str]


def _sense(sense: Sense) -> SenseItem:
    return SenseItem(
        term=sense.term,
        theme_kind=sense.theme_kind,
        origin=sense.origin.value,
        section=sense.section,
        scope=sense.scope.token,
        raised_by=str(sense.properties.get("raised_by") or "") or None,
    )


def _corrections(context: V1Context, word: str) -> list[CorrectionItem]:
    """A person's corrections of the word in force, one per scope: the whole project first."""
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.questions import NAMING_KINDS, term_target

    own = term_target(word)
    found: list[CorrectionItem] = []
    for target, event in sorted(latest_by_target(read_events(context.project)).items()):
        if not (target == own or target.startswith(f"{own} below ")):
            continue
        if event.kind not in NAMING_KINDS:
            continue
        try:
            payload = json.loads(event.payload)
        except (TypeError, ValueError):
            continue
        corrected = payload.get("corrects")
        found.append(
            CorrectionItem(
                sequence=int(event.sequence),
                actor=event.actor,
                occurred_at=event.occurred_at,
                kind=event.kind.value,
                term=str(payload.get("answer") or ""),
                theme_kind=str(payload.get("theme_kind") or ""),
                below=event.scope.value if event.scope.value else None,
                corrected=SenseItem(
                    term=str(corrected.get("term") or ""),
                    theme_kind=str(corrected.get("theme_kind") or ""),
                    origin=str(corrected.get("origin") or ""),
                    section=str(corrected.get("section") or ""),
                    scope=str(corrected.get("scope") or "project"),
                )
                if isinstance(corrected, dict)
                else None,
            )
        )
    return sorted(found, key=lambda item: (item.below is not None, item.below or ""))


@route("word/meaning", model=WordMeaning)
def meaning(context: V1Context, query: Mapping[str, Sequence[str]]) -> WordMeaning:
    """The word's meaning now, where it stands, and the correction in force. Nothing is written."""
    from ingence.api.routes.review import _theme_kinds
    from ingence.dictionary.store import DictionaryStore

    word = (one(query, "word") or "").strip()
    if not word:
        message = "name the word (word=...)"
        raise ApiError(message, parameter="word")
    with pinned_catalog(context) as (catalog, run_id):
        rows = catalog.query(
            """
            SELECT coalesce(nullif(regexp_replace(e.relative_path, '/[^/]*$', ''),
                                   e.relative_path), '') AS folder,
                   count(DISTINCT c.entry_id) AS files
            FROM components c JOIN entries e ON e.entry_id = c.entry_id
            WHERE lower(c.normalized_value) = ?
            GROUP BY folder ORDER BY files DESC, folder
            """,
            [word.casefold()],
        ).fetchall()
        kinds = _theme_kinds(catalog)
    if not rows:
        message = f"no name in run {run_id} carries the word {word!r}"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="word")
    store = DictionaryStore.for_project(context.project)
    hit = store.look_up(word)
    standing = "unbound" if hit is None else ("contested" if hit.ambiguous else "settled")
    return WordMeaning(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        word=word.casefold(),
        files=sum(int(files) for _, files in rows),
        folders=[FolderCount(path=str(path), files=int(files)) for path, files in rows][
            :FOLDERS_SHOWN
        ],
        folders_not_shown=max(0, len(rows) - FOLDERS_SHOWN),
        standing=standing,
        in_force=_sense(hit.sense) if hit is not None else None,
        tied=[_sense(sense) for sense in hit.alternatives] if hit is not None else [],
        outranked=[_sense(sense) for sense in hit.shadowed] if hit is not None else [],
        corrections=_corrections(context, word.casefold()),
        theme_kinds=list(kinds),
    )
