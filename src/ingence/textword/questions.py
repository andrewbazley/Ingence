"""One question per unbound word, every place it stands gathered beneath it.

A name that fails to read usually fails because of its content, not its position. So a person
answers one word at a time. Once the words are confirmed, positional and reagent senses are easier
to interpret. When several places raise the same positional question, one answer settles
all of them.

The unit a person answers is a word: *What is ``kelt``? It stands where ``atp``, ``adp`` and
``tcep`` stand, in 4 folders.* Each place a word stands beside words of one kind (one folder's
template, one part of it) is an :class:`Appearance` gathered beneath the one question. A word
raised in six folders is asked once, and one answer (a definition of ``term:kelt``) settles it
everywhere. The kind is a hint and follows from the answer, never the other way round: the
person says what the word *is*.

The statistic is the engine's own reading. Engine code computes it from the Index,
deterministically, and attributes it to the engine (:data:`READER`). It states counts and
neighbours and never a meaning. Nothing here reads any event but a person's answers, and those
only to say what is already answered: the questions asked are the run's findings and the Index's
names.

A definition applies everywhere by default, because that is what a word's meaning usually is in a
laboratory. The other choice is "only where it stands" (a scoped definition). One function says
where a word stands (:func:`raised_folders`: every folder the run raised it in where no defeater
fired, uncapped). The page offers the choice, and the route that writes it
(``api/routes/decide.py``) applies it. Both read the places through :func:`where_it_stands`, so
the page never offers a folder that the route refuses.

The page function :func:`word_question_page` reads a catalog and the project's Record and returns
plain values. The Questions page renders them, and writes an answer through the one route that
writes (``api/routes/decide.py``).
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ingence.core.settings import SpellingSettings
from ingence.dictionary.spelling import SpellingIndex
from ingence.textword import TEXTWORD_VERSION
from ingence.textword.slots import (
    BOUND,
    DEFAULTS,
    UNBOUND,
    WORD,
    WORDS_SHOWN,
    AlignedName,
    PlaceReading,
    Slot,
    listed,
    paradigms,
    places_of_a_kind,
    plural,
    said_as_a_kind,
    spelling_test,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = [
    "APPEARANCES_SHOWN",
    "DEFAULTS",
    "EVERYWHERE",
    "NOISE_SECTIONS",
    "PREDICATE",
    "READER",
    "RULE_ID",
    "WHERE_IT_STANDS",
    "Appearance",
    "RaisedFolder",
    "WhereItStands",
    "WordQuestion",
    "answer_scope",
    "choices_for",
    "declared_word_rules",
    "engine_reading",
    "names_from_catalog",
    "noise_spellings",
    "raised_folders",
    "scope_where_it_stands",
    "where_it_stands",
    "word_question_page",
    "word_questions",
]

#: How the engine's reading is attributed on the page, beside a person's or a reviewer's.
READER = f"ingence.textword {TEXTWORD_VERSION}"

#: The rule that raises these words as findings (revision 4).
RULE_ID = "unbound_word_in_a_kinds_place"

#: What the rule tests: how the Questions page knows a row is a word question, whatever a
#: project calls the rule.
PREDICATE = "unbound_word_where_a_kind_stands"

#: The two places a definition from a word question may apply. The first is the default.
EVERYWHERE = "everywhere"
WHERE_IT_STANDS = "where_it_stands"

#: How many places travel with a question. The counts are exact. The rest are counted.
APPEARANCES_SHOWN = 12

#: How many questions one page of the function returns. A person answers one at a time.
LIMIT = 100

#: The Dictionary sections whose senses name files nobody chose to make (operating systems,
#: editors and sync clients: ``Thumbs.db``, ``desktop.ini``). A name the run's own Dictionary
#: spells as one of them is left out of the alignment, so a word such as ``thumbs`` is never
#: asked about for standing where ``atp.tif`` stands. The spellings are the Dictionary's. Only the
#: section is named here, as a parameter.
NOISE_SECTIONS: tuple[str, ...] = ("filesystem_noise",)

#: How many computed pages the process keeps, each by the run it read (the reading takes seconds
#: on a large census, and every Questions page and every tab count asks for it).
KEPT_RUNS = 4


@dataclass(frozen=True, slots=True)
class Appearance:
    """One place a word stands beside words of one kind: a folder's template, one part of it."""

    folder_id: str
    where: str
    alongside: tuple[str, ...]
    kind: str
    #: How many bound words there share the kind, of how many are bound.
    agreeing: int
    bound: int
    #: The template and the part of it: two templates of one folder are two places.
    template: tuple[str, ...] = ()
    position: int = 0
    example: str = ""
    #: How many names carry the word at this place.
    files: int = 0


@dataclass(frozen=True, slots=True)
class WordQuestion:
    """One unbound word, asked once, every place it stands like a kind gathered beneath it."""

    word: str
    appearances: tuple[Appearance, ...]
    #: The kind most places agree on: a hint, never the answer.
    kind: str | None
    #: How many places lean to ``kind``, of how many there are.
    agreeing: int
    #: How many places were gathered before the cap.
    places: int
    #: Every folder it stands in, uncapped.
    folder_ids: tuple[str, ...] = ()
    #: How many names carry it, over every place.
    files: int = 0

    @property
    def folders(self) -> tuple[str, ...]:
        return self.folder_ids or tuple(sorted({a.folder_id for a in self.appearances}))

    @property
    def alongside(self) -> tuple[str, ...]:
        """The bound words beside it, most often first, then by spelling."""
        seen = Counter(word for a in self.appearances for word in a.alongside)
        return tuple(word for word, _ in sorted(seen.items(), key=lambda item: (-item[1], item[0])))

    def evidence(self, *, words_shown: int = WORDS_SHOWN) -> str:
        """Where it stands, in one sentence: the words beside it and how widely it stands."""
        beside = self.alongside
        stand = "stands" if len(beside) == 1 else "stand"
        return (
            f"It stands where {listed(beside, shown=words_shown)} {stand}, "
            f"{_where(self.places, len(self.folders))}."
        )

    def sentence(self, *, words_shown: int = WORDS_SHOWN) -> str:
        """The question as a page asks it, the evidence after it. The kind, a hint, is the
        engine's reading's to say (:func:`engine_reading`), never the question's."""
        return f"What is {self.word}? {self.evidence(words_shown=words_shown)}"


def _where(places: int, folders: int) -> str:
    """How widely a word stands, in words: the folders first (what a person can picture), and the
    places only where they are more than the folders (one folder may write its names more than
    one way, and a word may stand at two parts of one way)."""
    folder_words = f"{folders} {'folder' if folders == 1 else 'folders'}"
    if places == folders:
        return f"in {folder_words}"
    return f"in {folder_words}, at {places} places in names written alike"


def word_questions(
    readings: Iterable[PlaceReading],
    *,
    appearances_shown: int = APPEARANCES_SHOWN,
    paths: Mapping[str, str] | None = None,
) -> list[WordQuestion]:
    """One question per unbound word, from every place that raised it.

    Words are folded to one spelling. Ordered as a queue wants them: a word whose places agree on
    one kind first, then by how widely it stands (places, then folders), then by spelling, so the
    same folders always give the same order. A word's places are listed by their folders' paths
    where ``paths`` gives them (a person reads a list of folders in that order), and the first
    ``appearances_shown`` travel with it.
    """
    where_is = paths or {}
    gathered: dict[str, dict[tuple[str, tuple[str, ...], int], Appearance]] = {}
    for reading in readings:
        for word in reading.unbound:
            at = (reading.folder_id, reading.shape, reading.position)
            held = gathered.setdefault(word.casefold(), {})
            earlier = held.get(at)
            files = reading.counts.get(word, 0) + (earlier.files if earlier else 0)
            held[at] = Appearance(
                folder_id=reading.folder_id,
                where=reading.where(),
                alongside=reading.alongside,
                kind=reading.kind,
                agreeing=reading.agreeing,
                bound=reading.bound,
                template=reading.shape,
                position=reading.position,
                example=reading.example,
                files=files,
            )
    found: list[WordQuestion] = []
    for word, held in gathered.items():
        places = list(held.values())
        kinds = Counter(a.kind for a in places)
        (kind, agreeing), *_ = sorted(kinds.items(), key=lambda item: (-item[1], item[0]))
        ordered = sorted(
            places,
            key=lambda a: (
                a.kind != kind,
                where_is.get(a.folder_id, ""),
                a.folder_id,
                a.template,
                a.position,
                a.alongside,
            ),
        )
        found.append(
            WordQuestion(
                word=word,
                appearances=tuple(ordered[:appearances_shown]),
                kind=kind,
                agreeing=agreeing,
                places=len(places),
                folder_ids=tuple(sorted({a.folder_id for a in places})),
                files=sum(a.files for a in places),
            )
        )
    found.sort(key=lambda q: (q.agreeing < q.places, -q.places, -len(q.folder_ids), q.word))
    return found


def engine_reading(
    question: WordQuestion,
    *,
    paths: Mapping[str, str] | None = None,
    words_shown: int = WORDS_SHOWN,
) -> dict[str, Any]:
    """What a page shows beside a word question as the engine's own reading of where it stands.

    States counts and neighbours and the kind they lean to, and says that the kind is a hint and
    never a meaning. ``paths`` maps a folder identifier to its path, for the places' display.
    """
    kind = question.kind
    agreeing, places = question.agreeing, question.places
    if places == 1:
        at = "Where it stands"
    elif agreeing == places:
        at = "At both places it stands" if places == 2 else f"At all {places} places it stands"
    else:
        at = f"At {agreeing} of the {places} places it stands"
    return {
        "by": READER,
        # The question's own sentence says where it stands. The engine's reading says what the
        # words beside it lean to, and that it is a hint.
        "reading": (
            f"{at}, most of the words the Dictionary knows beside it are {plural(kind)}: a hint, "
            "not an answer."
            if kind
            else "The words beside it lean to no one kind."
        ),
        "kind_hint": said_as_a_kind(kind) if kind else None,
        "leaning": question.agreeing,
        "of": question.places,
        "appearances": [
            {
                "folder_id": a.folder_id,
                "folder": _shown_path((paths or {}).get(a.folder_id, a.folder_id)),
                "where": a.where,
                "example": a.example,
                "files": a.files,
                "alongside": list(a.alongside[:words_shown]),
                "alongside_not_shown": max(0, len(a.alongside) - words_shown),
                "kind": said_as_a_kind(a.kind),
                "bound_agreeing": a.agreeing,
                "bound": a.bound,
            }
            for a in question.appearances
        ],
    }


def _shown_path(path: str) -> str:
    """A folder's path as a page shows it: the top of a folder the project reads as '.'."""
    return path if path.strip("./") else "."


# --- where a word stands, and where a meaning kept to it would apply ------------------------


@dataclass(frozen=True, slots=True)
class RaisedFolder:
    """One folder a rule raised a value in: a folder finding's own, or a file finding's parent."""

    folder_id: str
    #: Relative to its root, as the census writes it. Empty for the top of a root.
    path: str
    root_id: str
    #: Every finding there carries a defeater that fired (beneath an installed program).
    set_aside: bool = False


def _normal(path: str | None) -> str:
    spelled = str(path or "").strip("/")
    return "" if spelled in ("", ".") else spelled


def raised_folders(
    catalog: Catalog, *, rule_id: str | None = None, raised: str | None = None
) -> dict[tuple[str, str], tuple[RaisedFolder, ...]]:
    """Every folder the run raised each (rule, value) in, uncapped, each marked set aside or not.

    This is the one reading of where a question stands, for folder findings and file findings
    alike: a folder finding stands in its folder, a file finding in the folder holding the file,
    and a root finding at the top of its root. A folder is set aside where every finding there
    carries a defeater that fired, which is how the Review queue sets a question aside beneath an
    installed program. Canonical order, by path.
    """
    columns = {str(row[0]) for row in catalog.query("DESCRIBE SELECT * FROM findings").fetchall()}
    fired = (
        "coalesce(list_bool_or(CAST(json_extract(coalesce(f.defeaters_json, '[]'), "
        "'$[*].fired') AS BOOLEAN[])), FALSE)"
        if "defeaters_json" in columns
        else "FALSE"
    )
    conditions: list[str] = []
    values: list[str] = []
    if rule_id is not None:
        conditions.append("f.rule_id = ?")
        values.append(rule_id)
    if raised is not None:
        conditions.append("u.value = ?")
        values.append(raised)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = catalog.query(
        f"""
        WITH asked AS (
            SELECT f.rule_id, u.value AS raised, f.subject, {fired} AS fired
            FROM findings f, unnest(f.raised) AS u(value) {where}
        ), placed AS (
            SELECT a.rule_id, a.raised, a.fired,
                   CASE WHEN a.subject LIKE 'folder:%'
                            THEN regexp_replace(a.subject, '^folder:', '')
                        WHEN a.subject LIKE 'root:%' THEN NULL
                        ELSE s.parent_entry_id END AS folder_id,
                   CASE WHEN a.subject LIKE 'root:%' THEN regexp_replace(a.subject, '^root:', '')
                        END AS root_alone
            FROM asked a LEFT JOIN entries s ON s.entry_id = a.subject
        )
        SELECT p.rule_id, p.raised, coalesce(p.folder_id, ''), d.relative_path,
               coalesce(d.root_id, p.root_alone) AS root_id, bool_and(p.fired)
        FROM placed p LEFT JOIN entries d ON d.entry_id = p.folder_id
        GROUP BY 1, 2, 3, 4, 5
        """,  # noqa: S608 - the interpolations are constant expressions and placeholders
        values,
    ).fetchall()
    found: dict[tuple[str, str], list[RaisedFolder]] = {}
    for rule, value, folder, path, root, aside in rows:
        if root is None:
            continue
        found.setdefault((str(rule), str(value)), []).append(
            RaisedFolder(
                folder_id=str(folder),
                path=_normal(path),
                root_id=str(root),
                set_aside=bool(aside),
            )
        )
    return {
        key: tuple(sorted(folders, key=lambda item: (item.root_id, item.path, item.folder_id)))
        for key, folders in sorted(found.items())
    }


def answer_scope(folder_paths: Sequence[str]) -> str | None:
    """The deepest folder holding every one of these folders, or None where that is the top.

    A folder scope covers the folder and what lies beneath it, so a meaning kept to this
    folder never reaches past the places it was asked about. The top of a folder the project
    reads (written ``.`` by the census) is not a folder that a scope can name, so the answer is
    None.
    """
    split = [[part for part in _normal(path).split("/") if part] for path in folder_paths]
    if not split:
        return None
    common: list[str] = []
    for parts in zip(*split, strict=False):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return "/".join(common) or None


@dataclass(frozen=True, slots=True)
class WhereItStands:
    """Where a word stands, and where a meaning kept to that would apply, or why only everywhere."""

    #: Every folder it stands in outside installed programs, uncapped, by path.
    folders: tuple[RaisedFolder, ...]
    #: The folder a meaning kept to where it stands applies in (and beneath), or None.
    folder: str | None
    #: In plain words: where the kept meaning applies, or why a meaning here applies everywhere.
    because: str


def where_it_stands(
    places: Iterable[RaisedFolder],
    *,
    roots: int,
    held_elsewhere: Callable[[str, str], bool],
) -> WhereItStands:
    """Where a meaning kept to where a word stands would apply, from where the run raised it.

    A folder scope is refused, in plain words, wherever it would reach past those places. That
    is the case where they lie in more than one of the folders the project reads (a scope names a
    path within one of them, and would reach the same path in every other), where the deepest
    folder holding them is the top of one (no folder names it; with one such folder that is
    everywhere anyway), and where another of the project's folders holds a folder of the same
    path. ``roots`` is how many folders the project reads, and ``held_elsewhere(path, root)``
    whether another holds ``path``.
    """
    kept = tuple(place for place in places if not place.set_aside)
    if not kept:
        return WhereItStands(
            folders=(),
            folder=None,
            because="It is asked about nowhere outside installed programs, so a meaning given "
            "here applies everywhere.",
        )
    if len({place.root_id for place in kept}) > 1:
        return WhereItStands(
            folders=kept,
            folder=None,
            because="It stands in more than one of the folders this project reads, and a "
            "meaning can be kept to one folder and what lies beneath it, not to several, so it "
            "applies everywhere.",
        )
    common = answer_scope([place.path for place in kept])
    if common is None:
        because = (
            "Kept to where it stands, a meaning would reach every folder anyway, so it applies "
            "everywhere."
            if roots <= 1
            else "It stands at the top of one of the folders this project reads, and a meaning "
            "cannot be kept to the top of one of them, so it applies everywhere."
        )
        return WhereItStands(folders=kept, folder=None, because=because)
    if roots > 1 and held_elsewhere(common, kept[0].root_id):
        return WhereItStands(
            folders=kept,
            folder=None,
            because=f"Another of the folders this project reads also holds a folder {common}, and "
            "a meaning kept to it would reach both, so it applies everywhere.",
        )
    return WhereItStands(folders=kept, folder=common, because=f"only in {common} and below")


def _roots(catalog: Catalog) -> int:
    counted = catalog.query("SELECT count(DISTINCT root_id) FROM roots").fetchone()
    return int(counted[0]) if counted else 0


def _held_elsewhere(catalog: Catalog) -> Callable[[str, str], bool]:
    def held(path: str, root_id: str) -> bool:
        found = catalog.query(
            "SELECT 1 FROM entries WHERE root_id <> ? AND NOT is_deleted "
            "AND (relative_path = ? OR starts_with(relative_path, ? || '/')) LIMIT 1",
            [root_id, path, path],
        ).fetchone()
        return found is not None

    return held


def scope_where_it_stands(
    catalog: Catalog,
    rule_id: str,
    raised: str,
    *,
    placed: Mapping[tuple[str, str], Sequence[RaisedFolder]] | None = None,
) -> WhereItStands:
    """Where one question's word stands, read from the run: what the page offers and the route
    that writes reads. ``placed`` is :func:`raised_folders` already read, for a page of many."""
    if placed is None:
        placed = raised_folders(catalog, rule_id=rule_id, raised=raised)
    return where_it_stands(
        placed.get((rule_id, raised), ()),
        roots=_roots(catalog),
        held_elsewhere=_held_elsewhere(catalog),
    )


def choices_for(stands: WhereItStands) -> list[dict[str, Any]]:
    """Where a meaning may apply, as a page offers it: everywhere, and a folder where one can."""
    offered: list[dict[str, Any]] = [{"value": EVERYWHERE, "label": "everywhere", "default": True}]
    if stands.folder is not None:
        offered.append(
            {
                "value": WHERE_IT_STANDS,
                "label": stands.because,
                "default": False,
                "scope_folder": stands.folder,
            }
        )
    return offered


# --- the page function ----------------------------------------------------------------------


def _slot(
    component_id: str,
    component_type: str,
    words: Mapping[str, tuple[str, str, str | None, str]],
) -> Slot:
    found = words.get(component_id)
    if found is None:
        return Slot(token="", component_type=component_type)
    token, status, kind, binding_id = found
    state = (
        BOUND
        if status == "resolved" and kind
        else UNBOUND
        if status == "examined_no_evidence"
        else None
    )
    return Slot(
        token=token,
        component_type=component_type,
        state=state,
        kind=kind if state == BOUND else None,
        records=(component_id, binding_id),
    )


def noise_spellings(
    catalog: Catalog, *, sections: Sequence[str] = NOISE_SECTIONS
) -> tuple[str, ...]:
    """The spellings of files nobody chose to make, from the run's own Dictionary."""
    columns = {
        str(row[0]) for row in catalog.query("DESCRIBE SELECT * FROM dictionary_entries").fetchall()
    }
    if "section" not in columns or not sections:
        return ()
    rows = catalog.query(
        "SELECT surfaces FROM dictionary_entries WHERE section IN (SELECT unnest(?::VARCHAR[])) "
        "AND coalesce(status, 'active') = 'active'",
        [list(sections)],
    ).fetchall()
    return tuple(
        sorted({str(surface) for (surfaces,) in rows for surface in surfaces or () if surface})
    )


def names_from_catalog(
    catalog: Catalog, *, leave_out: Sequence[str] = ()
) -> tuple[list[AlignedName], dict[str, str], dict[str, set[str]]]:
    """Every name of the current run as aligned slots, each folder's path, and what was left out.

    One pass over the census's name shapes (which hold the order a name was written in), the word
    components and their bindings. Words only carry a token. Other slots carry their type, which
    is all the alignment reads of them. A name spelled like one of ``leave_out`` (a file the
    operating system or a program writes) is left out, and its words are returned by folder.
    """
    words: dict[str, tuple[str, str, str | None, str]] = {
        str(component): (str(value), str(status), kind, str(binding))
        for component, value, status, kind, binding in catalog.query(
            "SELECT c.component_id, c.normalized_value, b.resolution_status, b.theme_kind, "
            "b.binding_id FROM components c JOIN dictionary_bindings b "
            "ON b.component_id = c.component_id WHERE c.component_type = 'word'"
        ).fetchall()
    }
    paths = {
        str(folder): str(path)
        for folder, path in catalog.query(
            "SELECT entry_id, relative_path FROM entries WHERE entry_type = 'directory'"
        ).fetchall()
    }
    names: list[AlignedName] = []
    left_out: dict[str, set[str]] = {}
    noise = spelling_test(leave_out)
    for entry_id, parent, name, slots_json in catalog.query(
        "SELECT s.entry_id, e.parent_entry_id, e.name, s.slots_json FROM name_shapes s "
        "JOIN entries e ON e.entry_id = s.entry_id "
        "WHERE s.source_scope = 'name' AND NOT e.is_deleted AND e.parent_entry_id IS NOT NULL "
        "ORDER BY s.entry_id"
    ).fetchall():
        slots = tuple(
            _slot(str(item["component_id"]), str(item["component_type"]), words)
            for item in json.loads(slots_json or "[]")
        )
        if leave_out and noise(str(name)):
            left_out.setdefault(str(parent), set()).update(
                slot.token for slot in slots if slot.component_type == WORD and slot.token
            )
            continue
        names.append(
            AlignedName(entry_id=str(entry_id), folder_id=str(parent), slots=slots, name=str(name))
        )
    return names, paths, left_out


@dataclass(frozen=True, slots=True)
class _Reading:
    """What one run's word questions are, before any page or answer: kept by the run."""

    questions: tuple[WordQuestion, ...]
    paths: Mapping[str, str]
    raised: Mapping[str, tuple[RaisedFolder, ...]]
    set_aside_beneath_programs: int
    left_out_as_noise: int


_KEPT: dict[str, _Reading] = {}
_KEPT_LOCK = threading.Lock()


def _read(
    catalog: Catalog,
    rule_id: str,
    settings: Mapping[str, object],
    noise_sections: Sequence[str],
    appearances_shown: int,
) -> _Reading:
    """The run's word questions, computed once per run, rule, parameters and caps.

    The reading is kept by the run it read. It takes seconds on a large census, and every
    Questions page and every tab count asks for it, while nothing it reads changes within a
    published run. What a person has answered is read on every call, never kept.
    """
    key = json.dumps(
        [
            str(catalog.run_directory),
            rule_id,
            {name: str(value) for name, value in sorted(settings.items())},
            list(noise_sections),
            appearances_shown,
        ]
    )
    with _KEPT_LOCK:
        kept = _KEPT.get(key)
    if kept is not None:
        return kept
    names, paths, left_out = names_from_catalog(
        catalog, leave_out=noise_spellings(catalog, sections=noise_sections)
    )
    found = paradigms(names, minimum_names=int(str(settings["minimum_names"])))
    readings = places_of_a_kind(
        found,
        minimum_bound=int(str(settings["minimum_bound"])),
        agreement=float(str(settings["agreement"])),
    )
    # The run's findings are the queue's truth: a word is asked about where the rule raised it
    # and no defeater fired there (a word beneath an installed program is a program's, and the
    # Review queue sets it aside the same way). The tables give each place its reading.
    by_word = {
        value: folders for (_, value), folders in raised_folders(catalog, rule_id=rule_id).items()
    }
    asked = {(f.folder_id, word) for word, held in by_word.items() for f in held if not f.set_aside}
    aside = {(f.folder_id, word) for word, held in by_word.items() for f in held if f.set_aside}
    kept_readings: list[PlaceReading] = []
    set_aside = 0
    for reading in readings:
        words = tuple(w for w in reading.unbound if (reading.folder_id, w) in asked)
        set_aside += sum(1 for w in reading.unbound if (reading.folder_id, w) in aside)
        if words:
            kept_readings.append(replace(reading, unbound=words))
    questions = word_questions(kept_readings, appearances_shown=appearances_shown, paths=paths)
    # The folders a question stands in are the run's own (uncapped): the sentence counts them, and
    # the choice of where a meaning applies is computed from them, as the route that writes does.
    raised = {word: tuple(f for f in held if not f.set_aside) for word, held in by_word.items()}
    questions = [
        replace(q, folder_ids=tuple(f.folder_id for f in raised.get(q.word, ())) or q.folder_ids)
        for q in questions
    ]
    offered = {q.word for q in questions}
    noise_words = {word.casefold() for held in left_out.values() for word in held}
    left_out_words = sum(
        1 for word in raised if word not in offered and raised[word] and word in noise_words
    )
    computed = _Reading(
        questions=tuple(questions),
        paths=paths,
        raised=raised,
        set_aside_beneath_programs=set_aside,
        left_out_as_noise=left_out_words,
    )
    with _KEPT_LOCK:
        _KEPT[key] = computed
        while len(_KEPT) > KEPT_RUNS:
            _KEPT.pop(next(iter(_KEPT)))
    return computed


def declared_word_rules(project: Path) -> list[str]:
    """The rules in force in a project that ask about words, whether or not a run evaluated them.

    Read from the project's Rulebook (not cached, as a Rulebook never is), for a page to say why
    no word is asked yet where the last run never evaluated such a rule.
    """
    from ingence.rulebook import project_rulebook

    return sorted(
        rule.rule_id for rule in project_rulebook(project).rules if rule.predicate == PREDICATE
    )


def _run_parameters(catalog: Catalog, rule_id: str) -> dict[str, object] | None:
    """The rule's parameters as the run used them, or None where the run never evaluated it.

    Read from the run's own record of its rules, as the Rules page reads them, and decoded by the
    one function that decodes a stored parameter (:func:`~ingence.rulebook.render.value_in_force`).
    """
    from ingence.core.schema import Rule
    from ingence.rulebook.render import value_in_force
    from ingence.storage.index_store import record_from_row

    rows = (
        catalog.query("SELECT * FROM rules WHERE rule_id = ? ORDER BY version", [rule_id])
        .to_arrow_table()
        .to_pylist()
    )
    if not rows:
        return None
    rule = record_from_row(rows[-1], Rule)
    return {spec.name: value_in_force(spec) for spec in rule.parameters if spec.name in DEFAULTS}


def _not_yet_asked(catalog: Catalog, rule_id: str) -> str | None:
    """Why no word is asked about yet, where the run never evaluated the rule. None where it did.

    Nothing is offered before the rule has run. The questions are the run's findings, so a page
    built from the names alone would offer questions whose every answer is refused, with the
    words beneath installed programs not yet set aside.
    """
    evaluated = catalog.query("SELECT 1 FROM rules WHERE rule_id = ? LIMIT 1", [rule_id]).fetchone()
    if evaluated is not None:
        return None
    inferred = catalog.query("SELECT count(*) FROM rules").fetchone()
    if not inferred or not inferred[0]:
        return (
            "No inference has run since the names were read, so no word has been asked about "
            "yet. Run the inference and the questions appear here."
        )
    return (
        "The last inference ran before this rule was in force, so no word has been asked about "
        "yet. Run the inference again and the questions appear here."
    )


def word_question_page(
    catalog: Catalog,
    project: Path | None = None,
    *,
    rule_id: str = RULE_ID,
    parameters: Mapping[str, object] | None = None,
    limit: int = LIMIT,
    offset: int = 0,
    appearances_shown: int = APPEARANCES_SHOWN,
    words_shown: int = WORDS_SHOWN,
    noise_sections: Sequence[str] = NOISE_SECTIONS,
) -> dict[str, Any]:
    """The word questions of the current run, as plain values a page renders.

    The Questions page calls it (``api/routes/pages.py``). Each entry of ``questions`` carries
    what a row shows: ``word``, ``sentence``, ``reading`` (the engine's own line, shown as such),
    ``appearances`` (each place, with its folder's path; ``appearances_shown`` of them, the rest
    counted), ``folders`` (every folder it stands in), ``choices`` (where a definition applies:
    everywhere, and only where it stands where a folder can hold that), and ``question_target``,
    which a decision is written against (``decide_target``, with ``where`` set to a choice's
    ``value``). ``in_force`` is what a person has already answered. The parameters are the ones
    the run used where it evaluated the rule (the project's Rulebook is then not read at all).
    Otherwise they are the project's rule's, where ``project`` is given, or :data:`DEFAULTS`.
    Nothing is offered until the run has evaluated the rule, and ``waiting`` says why. The
    reading is kept by the run it read.
    """
    in_force_rule = True
    settings: dict[str, object] = dict(DEFAULTS)
    # The parameters the run used, where it evaluated the rule: the questions are its
    # findings, so their places are read at the values the findings were raised at, whatever the
    # project's Rulebook says now, and a Rulebook that no longer loads leaves the page readable.
    used = _run_parameters(catalog, rule_id) if catalog.has_run else None
    if used is not None:
        settings.update(used)
    elif project is not None:
        from ingence.infer.evaluate import parameters_in_force
        from ingence.rulebook import project_rulebook

        rules = {rule.rule_id: rule for rule in project_rulebook(project).rules}
        rule = rules.get(rule_id)
        if rule is None:
            in_force_rule = False
        else:
            in_force = parameters_in_force(rule)
            settings.update({name: in_force[name] for name in DEFAULTS})
    settings.update(parameters or {})
    base: dict[str, Any] = {"rule_id": rule_id, "by": READER, "parameters": settings}
    if not in_force_rule or not catalog.has_run:
        return {
            **base,
            "in_force": False,
            "asked": False,
            "waiting": "The rule that asks about words is not in force in this project.",
            "total": 0,
            "questions": [],
        }
    run = catalog.query("SELECT run_id FROM run").fetchone()
    base["run_id"] = str(run[0]) if run else None
    waiting = _not_yet_asked(catalog, rule_id)
    if waiting is not None:
        return {
            **base,
            "in_force": True,
            "asked": False,
            "waiting": waiting,
            "total": 0,
            "offset": offset,
            "questions": [],
        }
    reading = _read(catalog, rule_id, settings, noise_sections, appearances_shown)
    answered = _answers(project, rule_id) if project is not None else {}
    roots = _roots(catalog)
    held_elsewhere = _held_elsewhere(catalog)
    spellings = spelling_index(catalog, _spelling_settings(project))
    rows: list[dict[str, Any]] = []
    for question in reading.questions[offset : offset + limit]:
        stands = where_it_stands(
            reading.raised.get(question.word, ()), roots=roots, held_elsewhere=held_elsewhere
        )
        shown = engine_reading(question, paths=reading.paths, words_shown=words_shown)
        rows.append(
            {
                "word": question.word,
                "sentence": question.sentence(words_shown=words_shown),
                "evidence": question.evidence(words_shown=words_shown),
                "target": f"term:{question.word}",
                "question_target": f"question:{rule_id}:{question.word}",
                "reading": shown,
                "appearances": shown["appearances"],
                "places": question.places,
                "places_not_shown": max(0, question.places - len(question.appearances)),
                "files": question.files,
                "folders": [_shown_path(folder.path) for folder in stands.folders],
                "choices": choices_for(stands),
                "where": {"folder": stands.folder, "because": stands.because},
                "in_force": answered.get(question.word),
                # The known word that this word is one slip from, shown beside the question
                # for a person to take or leave, never bound.
                "spelling_proposed": _proposed(spellings, question.word),
                # Every known word that this word may be a slip of, and the page's sentence.
                "spellings_proposed": (proposed := _candidates(spellings, question.word)),
                "did_you_mean": did_you_mean(proposed),
            }
        )
    not_asked = []
    if reading.set_aside_beneath_programs:
        count = reading.set_aside_beneath_programs
        not_asked.append(
            f"{count} {'place' if count == 1 else 'places'} beneath installed programs, which "
            "are the programs' own words"
        )
    if reading.left_out_as_noise:
        count = reading.left_out_as_noise
        not_asked.append(
            f"{count} {'word' if count == 1 else 'words'} standing only in names the operating "
            "system or a program writes, such as Thumbs.db"
        )
    return {
        **base,
        "in_force": True,
        "asked": True,
        # The reading is computed from the whole run (seconds on a large census) and kept by it.
        "source": "the run's findings",
        "places_set_aside_beneath_programs": reading.set_aside_beneath_programs,
        "words_left_out_as_noise": reading.left_out_as_noise,
        "not_asked": not_asked,
        "total": len(reading.questions),
        "offset": offset,
        "questions": rows,
    }


#: The spelling indexes built, by run, most recent last: a run's vocabulary never changes, and the
#: Questions page asks for the index on every request.
_SPELLINGS: dict[str, SpellingIndex] = {}
_SPELLINGS_LOCK = threading.Lock()


def spelling_index(catalog: Catalog, spelling: SpellingSettings | None = None) -> SpellingIndex:
    """The run's own Dictionary's spellings, for proposing a word's spelling.

    Built once per run and kept, as the page's reading is (:data:`KEPT_RUNS` of them), with the
    project's thresholds (``dictionary.spelling``).
    """
    limits = spelling or SpellingSettings()
    # Keyed by the run's own directory, which names its project too: two projects may hold runs
    # of one identifier, and their vocabularies differ. It is also keyed by the thresholds it was
    # built with.
    key = (
        f"{catalog.run_directory}|{limits.edits}|{limits.minimum_letters}|{limits.candidates}"
        if catalog.has_run and catalog.run_directory
        else ""
    )
    with _SPELLINGS_LOCK:
        found = _SPELLINGS.get(key) if key else None
    if found is not None:
        return found
    rows = catalog.query(
        "SELECT term, surfaces FROM dictionary_entries WHERE coalesce(status, 'active') = 'active'"
    ).fetchall()
    built = SpellingIndex(
        (
            (str(term), str(surface))
            for term, surfaces in rows
            for surface in surfaces or ()
            if surface
        ),
        minimum=limits.minimum_letters,
        edits=limits.edits,
        candidates=limits.candidates,
    )
    if key:
        with _SPELLINGS_LOCK:
            _SPELLINGS[key] = built
            while len(_SPELLINGS) > KEPT_RUNS:
                del _SPELLINGS[next(iter(_SPELLINGS))]
    return built


def _spelling_settings(project: Path | None) -> SpellingSettings | None:
    """A project's spelling thresholds (``dictionary.spelling``), or None for the defaults."""
    if project is None:
        return None
    from ingence.core.project import Project
    from ingence.storage.layout import PROJECT_FILE

    if not (project / PROJECT_FILE).is_file():
        return None
    return Project.open(project).settings.dictionary.spelling


def _proposed(spellings: SpellingIndex, word: str) -> dict[str, Any] | None:
    found = spellings.propose(word)
    if found is None:
        return None
    return {
        "spelling": found.spelling,
        "terms": list(found.terms),
        "sentence": f"Perhaps a slip for {found.spelling}, a word the Dictionary knows.",
    }


def _candidates(spellings: SpellingIndex, word: str) -> list[dict[str, Any]]:
    """The known words that a word may be a slip of, nearest first. They are never bound."""
    return [
        {"spelling": found.spelling, "terms": list(found.terms), "edits": found.edits}
        for found in spellings.candidates(word)
    ]


def did_you_mean(candidates: Sequence[Mapping[str, Any]]) -> str | None:
    """The page's sentence for a word's candidates, in plain words, or None where there are none."""
    spelled = [str(found["spelling"]) for found in candidates]
    if not spelled:
        return None
    if len(spelled) == 1:
        return f"Perhaps a slip for {spelled[0]}, a word the Dictionary knows."
    listed = ", ".join(spelled[:-1]) + f" or {spelled[-1]}"
    return f"Perhaps a slip for {listed}, words the Dictionary knows."


def _answers(project: Path, rule_id: str = RULE_ID) -> dict[str, dict[str, Any]]:
    """What a person has answered about each word: by the question, or by the word itself."""
    from ingence.record.log import latest_by_target, read_events

    latest = latest_by_target(read_events(project))
    said: dict[str, dict[str, Any]] = {}
    prefix = f"question:{rule_id}:"
    for target, event in latest.items():
        below = False
        if target.startswith(prefix):
            word = target[len(prefix) :]
        elif target.startswith("term:"):
            # ``term:<word> below <folder>`` is the word's meaning below a folder,
            # not a word called "<word> below <folder>": the word is what stands before " below ".
            word, kept_below, folder = target[len("term:") :].partition(" below ")
            below = bool(kept_below and folder)
        else:
            continue
        if below and word in said:
            continue  # the meaning for the whole project is the word's answer. A folder's is not
        try:
            payload = json.loads(event.payload)
        except (TypeError, ValueError):
            payload = {}
        said[word] = {
            "kind": event.kind.value,
            "answer": payload.get("answer"),
            "theme_kind": payload.get("theme_kind"),
            "scope": {"kind": event.scope.kind.value, "value": event.scope.value},
            "actor": event.actor,
        }
    return said
