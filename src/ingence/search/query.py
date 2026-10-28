"""Read what a person typed into the search box, the way the census reads a name.

The search uses one tokeniser, the same as the census (see ``dictionary/normalize.py``). A query is
read with the same :class:`~ingence.dictionary.normalize.Normaliser` that read every name in the
run, at the same version and with the same ``glued_runs`` reading. So ``SubX800ngmL2.5h`` typed
into the box is the label, the mass concentration and the duration that it is in a filename, and
``20min`` typed matches ``20 min`` written. A search that tokenised differently from the census
would silently miss the matches that it exists to find.

The search checks the version and does not assume it. The components that a run stored carry the
normaliser's version (``Component.normalizer_version``). The response reports a query read at
another version and does not compare it quietly. A partial rebind likewise refuses across a changed
tokeniser.

The grammar that a person may type is small:

- words, numbers, quantities, dates: ``SubX FRAP 500nM 2026-09``
- a quoted phrase: ``"spinning disk"``. The search reads it as its words and requires each one. It
  does not yet require that the words stand next to each other in a name, and the response says so.
- a field filter: ``type:image``, ``ext:nd2``, ``in:"Kelt series"``, ``stage:raw``,
  ``after:2026-01``, ``before:2026-06-30``, ``kind:reagent``, ``has:replicates``. ``after:`` means
  on or after the start of the day, month or year typed. ``before:`` means before that start.
  ``stage:raw`` (or ``stage:acquisition``) finds files at the stage where data enters.
  ``mark:poor`` (or ``mark:good``) finds units whose names carry that quality mark.
  ``topic:"Cell lines"`` finds units that are, or lie inside, an experiment of the person's topic
  of that name (or identifier). The search reads the topic from the Record when it runs, and
  nothing else reads a topic.
- an exclusion: ``-pooled``, or an excluded filter: ``-in:"Kelt series"``

The search reads anything else as words. It never silently refuses what a person typed. The
response returns a filter that the search does not know, or a value that it cannot read
(``after:yesterday``), in ``refused`` and shows it back to the person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from ingence.dictionary.normalize import Atom, AtomKind

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from ingence.dictionary.normalize import Normaliser
    from ingence.dictionary.store import DictionaryStore

__all__ = [
    "DEFAULT_FUNCTION_WORDS",
    "DROPPED_KINDS",
    "FILTER_NAMES",
    "MAX_CONCEPTS",
    "QUESTION_KIND",
    "FieldFilter",
    "ParsedQuery",
    "QueryTerm",
    "class_term",
    "is_question_word",
    "parse_query",
    "read_day",
]

#: The field filters a query may carry. Each is read by ``engine`` and nothing else.
FILTER_NAMES = frozenset(
    {"type", "ext", "in", "stage", "before", "after", "kind", "has", "mark", "topic"}
)

#: The most concepts that one query is read as. A person often pastes a whole filename into the
#: box, and this limit bounds that case. Every atom becomes a concept, and forty concepts would make
#: a tuple that nobody can read. The response returns the rest in ``refused`` with the reason.
MAX_CONCEPTS = 12

#: English function words that a person types between the words they mean ("FRAP of SubX plates").
#: The search drops them only where the Dictionary binds nothing to them and they are not inside a
#: quoted phrase. A laboratory whose Dictionary gives ``of`` a sense keeps it. This is a default
#: argument and not a constant read inside a function. If the list grows, it should move to a YAML
#: section beside ``negative_lexicon.yaml``, because domain vocabulary belongs in the Dictionary's
#: YAML and not in Python.
DEFAULT_FUNCTION_WORDS: tuple[str, ...] = (
    "a", "an", "and", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the",
    "to", "with", "without", "vs", "versus",
)  # fmt: skip

#: A quoted phrase, a filter with a quoted or bare value (excluded where a minus leads it), an
#: exclusion, or a bare run.
_PIECE = re.compile(
    r'(?P<phrase>"[^"]*")'
    r'|(?P<filter>(?P<negated>-)?(?P<fname>[A-Za-z]+):(?P<fvalue>"[^"]*"|[^\s"]*))'
    r"|(?P<exclude>-(?P<xvalue>[^\s\"]+))"
    r"|(?P<bare>[^\s\"]+)"
)

#: A day, a month or a year typed after ``after:`` or ``before:``: ``2026``, ``2026-06``,
#: ``2026-06-30``, with ``-``, ``/`` or ``.`` between.
_TYPED_DAY = re.compile(r"^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?$")

#: The values ``has:`` reads.
_HAS_VALUES = frozenset({"replicates", "replicate"})


def _marks(store: DictionaryStore) -> frozenset[str]:
    """The quality marks the vocabulary in force declares (``good``, ``poor``)."""
    from ingence.dictionary.quality_marks import quality_marks

    return frozenset(held.mark for held in quality_marks(store).values())


#: The axis of the Dictionary's connecting words (``connecting_words.yaml``). A function word bound
#: there only joins the words that a person means, and the search drops it.
CONNECTIVE_KIND = "connective"
#: The axis of the Dictionary's question words (``question_words.yaml``). ``does``, ``which`` and
#: ``compare`` say that a question is asked and never what it is about. The search drops them
#: wherever they stand, so they take none of the words that a search reads and are never "missing".
QUESTION_KIND = "question"
#: The kinds that the search drops a word as when the word is bound only to that kind. Such a word
#: relates the words that a person means.
DROPPED_KINDS = frozenset({CONNECTIVE_KIND, QUESTION_KIND})
#: The most words that a class phrase may have. ``nuclear stain`` has two. Three is the most.
CLASS_PHRASE_WORDS = 3


#: The shipped section where the question words are kept aside (``binds: false``). The binder never
#: sees them, and the search reads them here.
QUESTION_SECTION = "question_words"


def _joins_only(word: str, store: DictionaryStore | None) -> bool:
    """Return whether a function word means nothing here: unbound, or bound only as a connective."""
    if store is None:
        return True
    hit = store.look_up(word)
    return hit is None or hit.sense.theme_kind in DROPPED_KINDS


def is_question_word(word: str, store: DictionaryStore | None) -> bool:
    """Return whether a word is one that a question is asked with.

    Such a word is a spelling in the question-words section, which binds nothing in a name.
    """
    return store is not None and word.casefold() in store.aside_surfaces(QUESTION_SECTION)


def _bound_as(word: str, store: DictionaryStore | None, kinds: Collection[str]) -> bool:
    """Return whether the search drops a word for its kind.

    A question word is dropped wherever it stands, where ``question`` is among ``kinds``. In a
    question, the search also drops every word whose winning sense is of a kind that says how a
    file was made and not what it is about.
    """
    if store is None or not kinds:
        return False
    if QUESTION_KIND in kinds and is_question_word(word, store):
        return True
    hit = store.look_up(word)
    return hit is not None and hit.sense.theme_kind in kinds


def class_term(words: Sequence[str], store: DictionaryStore | None) -> str | None:
    """Return the Dictionary term that a run of typed words names as a class, or None.

    ``nuclear stain`` typed names the sense ``nuclear_stain``, whose members ``dapi`` and
    ``hoechst`` are its surfaces. The run names the sense by its term and never by a surface. So
    the search reads it as one concept and searches its members. A single word is a class the same
    way (``nucleotide``), and ``expand`` reads it. This function joins only a run of two or more
    words. It does not join a run that is a surface of some sense, because then the words are what
    was typed.
    """
    if store is None or len(words) < 2:
        return None
    joined = "_".join(word.casefold() for word in words)
    sense = store.sense_for_term(joined)
    if sense is None or sense.theme_kind in DROPPED_KINDS:
        return None
    return joined


def _join_class_phrases(atoms: list[Atom], store: DictionaryStore | None) -> list[Atom]:
    """Join adjacent words that name one class sense into one atom, longest run first."""
    if store is None:
        return atoms
    joined: list[Atom] = []
    index = 0
    while index < len(atoms):
        taken = False
        for length in range(CLASS_PHRASE_WORDS, 1, -1):
            run = atoms[index : index + length]
            if len(run) < length or any(atom.kind is not AtomKind.WORD for atom in run):
                continue
            term = class_term([atom.text for atom in run], store)
            if term is None:
                continue
            joined.append(
                replace(run[0], text=" ".join(atom.text for atom in run), normalised=term)
            )
            index += length
            taken = True
            break
        if not taken:
            joined.append(atoms[index])
            index += 1
    return joined


def read_day(value: str) -> str | None:
    """Return the first day that a typed year, month or day covers, as ``YYYY-MM-DD``.

    The result is None where the value is unreadable.
    """
    import datetime as dt

    found = _TYPED_DAY.match(value.strip())
    if found is None:
        return None
    year, month, day = int(found.group(1)), int(found.group(2) or 1), int(found.group(3) or 1)
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class FieldFilter:
    """One ``name:value`` restriction, as typed."""

    name: str
    value: str
    negated: bool = False


@dataclass(frozen=True, slots=True)
class QueryTerm:
    """One atom of the query and the concept it stands for."""

    #: The text as typed, for saying it back to the person.
    text: str
    #: The tokeniser's reading: the kind, the normalised form and, for a quantity, the base value.
    atom: Atom
    #: Which concept this atom is. Every atom outside a phrase is its own concept. The atoms of one
    #: phrase are separate concepts joined by an adjacency requirement.
    concept: int
    #: The phrase this atom belongs to, or None.
    phrase: int | None = None
    #: True for ``-word``. The search drops a unit that carries this concept and does not rank it.
    excluded: bool = False


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    """A query read, with what could not be read said plainly."""

    raw: str
    terms: tuple[QueryTerm, ...]
    filters: tuple[FieldFilter, ...]
    #: For each quoted phrase, its concepts in order. Each concept is required. The search does not
    #: yet require that they stand next to each other in a name, and the response says so.
    phrases: tuple[tuple[int, ...], ...]
    #: Pieces that the parser did not use, each with the reason, shown back to the person.
    refused: tuple[str, ...] = ()
    #: The normaliser version that the query was read at, compared with the run's.
    normalizer_version: str = ""
    #: The function words that the parser dropped, shown so that a person can see "of" was not
    #: searched for.
    dropped: tuple[str, ...] = field(default=())

    @property
    def concepts(self) -> tuple[int, ...]:
        """Return every concept that a unit is ranked by, in the order typed."""
        return tuple(sorted({term.concept for term in self.terms if not term.excluded}))


def _atoms_of(text: str, normaliser: Normaliser) -> list[Atom]:
    """Read one run of text, then rejoin a number the person spaced away from its unit.

    ``500 nM`` typed with a space reaches the tokeniser as a number and a word, because a space
    divides atoms. In a filename a person writes ``500nM``. This function rejoins them, and only
    here. It rejoins only where the word is wholly a unit that the Dictionary declares. So
    ``3 days`` becomes a duration and ``3 mice`` stays two atoms.
    """
    atoms = list(normaliser.atoms(text))
    joined: list[Atom] = []
    index = 0
    while index < len(atoms):
        current = atoms[index]
        following = atoms[index + 1] if index + 1 < len(atoms) else None
        if (
            current.kind is AtomKind.NUMBER
            and following is not None
            and following.kind is AtomKind.WORD
            and _is_whole_unit(following.text, normaliser)
        ):
            rejoined = normaliser.atoms(f"{current.text}{following.text}")
            if len(rejoined) == 1 and rejoined[0].kind is AtomKind.QUANTITY:
                joined.append(rejoined[0])
                index += 2
                continue
        joined.append(current)
        index += 1
    return joined


def _is_whole_unit(word: str, normaliser: Normaliser) -> bool:
    matched = normaliser.match_unit(word)
    return matched is not None and normaliser.consumed(word, matched[0]) == len(word)


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def parse_query(
    text: str,
    *,
    normaliser: Normaliser,
    store: DictionaryStore | None = None,
    function_words: tuple[str, ...] = DEFAULT_FUNCTION_WORDS,
    max_concepts: int = MAX_CONCEPTS,
    question: bool = False,
    dropped_kinds: Collection[str] = (QUESTION_KIND,),
) -> ParsedQuery:
    """Read a query into concepts, filters, phrases and exclusions.

    The function is pure. The same text, normaliser and vocabulary give the same reading, so a
    result page can be reproduced from its URL. View state lives in the URL.

    A word bound to one of ``dropped_kinds`` is dropped wherever it stands. By default these are the
    question words. As a question (``question``), a function word is dropped whatever the
    Dictionary binds it to, because ``in`` is never an inch in prose. The caller then widens
    ``dropped_kinds`` to the kinds that say how a file was made and not what it is about. Examples
    are ``statistic`` for ``change``, ``experiment`` for ``experiments`` and ``stage`` for
    ``results``.
    """
    terms: list[QueryTerm] = []
    filters: list[FieldFilter] = []
    phrases: list[tuple[int, ...]] = []
    refused: list[str] = []
    dropped: list[str] = []
    concept = 0
    unbound_words = frozenset(word.casefold() for word in function_words)

    def admit(atom: Atom, *, phrase: int | None, excluded: bool) -> int | None:
        nonlocal concept
        if concept >= max_concepts:
            refused.append(f"{atom.text!r}: only the first {max_concepts} words are searched")
            return None
        terms.append(
            QueryTerm(
                text=atom.text,
                atom=atom,
                concept=concept,
                phrase=phrase,
                excluded=excluded,
            )
        )
        concept += 1
        return concept - 1

    bare_run: list[str] = []

    def flush_bare() -> None:
        if not bare_run:
            return
        for atom in _join_class_phrases(_atoms_of(" ".join(bare_run), normaliser), store):
            if atom.kind is AtomKind.WORD and (
                (
                    atom.text.casefold() in unbound_words
                    and (question or _joins_only(atom.text, store))
                )
                or _bound_as(atom.text, store, dropped_kinds)
            ):
                dropped.append(atom.text)
                continue
            admit(atom, phrase=None, excluded=False)
        bare_run.clear()

    for match in _PIECE.finditer(text):
        if match.group("bare") is not None:
            bare_run.append(match.group("bare"))
            continue
        flush_bare()
        if match.group("phrase") is not None:
            inner = _unquote(match.group("phrase"))
            members: list[int] = []
            number = len(phrases)
            for atom in _atoms_of(inner, normaliser):
                admitted = admit(atom, phrase=number, excluded=False)
                if admitted is not None:
                    members.append(admitted)
            if len(members) > 1:
                phrases.append(tuple(members))
            elif not members:
                refused.append(f"{match.group('phrase')}: an empty phrase")
        elif match.group("filter") is not None:
            name = match.group("fname").casefold()
            value = _unquote(match.group("fvalue")).strip()
            negated = match.group("negated") is not None
            if name not in FILTER_NAMES:
                # The search does not know this filter. It reads the text as words and does not
                # drop it, because ``Rab5:GFP`` is a construct that somebody named and not a
                # field.
                if negated:
                    for atom in _atoms_of(match.group("filter")[1:], normaliser):
                        admit(atom, phrase=None, excluded=True)
                else:
                    bare_run.append(match.group("filter"))
            elif not value:
                refused.append(f"{match.group('filter')}: a filter needs a value")
            elif name in ("after", "before") and read_day(value) is None:
                refused.append(
                    f"{match.group('filter')}: a date is written as a year, a month or a day "
                    "(2026, 2026-06, 2026-06-30)"
                )
            elif name == "has" and value.casefold() not in _HAS_VALUES:
                refused.append(f"{match.group('filter')}: only has:replicates is read")
            elif name == "mark" and store is not None and value.casefold() not in _marks(store):
                known = ", ".join(f"mark:{mark}" for mark in sorted(_marks(store))) or "none"
                refused.append(
                    f"{match.group('filter')}: no word in the vocabulary is that mark ({known})"
                )
            else:
                filters.append(FieldFilter(name=name, value=value, negated=negated))
        elif match.group("exclude") is not None:
            for atom in _atoms_of(match.group("xvalue"), normaliser):
                admit(atom, phrase=None, excluded=True)
    flush_bare()

    return ParsedQuery(
        raw=text,
        terms=tuple(terms),
        filters=tuple(filters),
        phrases=tuple(phrases),
        refused=tuple(refused),
        normalizer_version=normaliser.version,
        dropped=tuple(dropped),
    )
