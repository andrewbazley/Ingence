"""Each typed word as a concept: the sense it names, the spellings it has, the variants a pool uses.

A search box that matches strings finds ``FRAP`` and misses ``fluorescence_recovery``. A search box
that matches meanings finds both, and says which it did. The Dictionary already holds the meanings,
because every sense lists its surfaces. Every run has already bound each name's words to them. So
expansion here is a lookup and not a guess.

Every match carries its route. The route is part of the ranking tuple and of the explanation. A
person reading a result can always see how it matched. It matched the word that they typed, or
another spelling of the sense that the word names, or a plural that the pool happens to use, or a
near spelling that the search offered only because nothing else matched. This is the Dashboard's
rule that every visible claim is one click from its justification, applied to a result row.

Routes, best first:

====================  ==========================================================================
``sense``             the typed word binds to a sense, so anything bound to that sense matches
``token``             the same normalised token, whether or not anything bound it
``quantity``          the same quantity however written (``500nM``, ``500 nM``, ``0.5uM``)
``date``              a date the typed date is a prefix of (``2026-09`` finds ``2026-09-23``)
``variant``           a plural or singular form the pool itself uses (``plates``/``plate``)
``piece``             a piece of a longer word the tokeniser kept whole (``SubX`` inside ``eSubX``)
``starts_with``       a word in a name beginning with the typed word (``zephyrine`` for ``zeph``)
``plain_name``        the typed word standing anywhere inside a name (``kozeph`` for ``zeph``)
``ambiguous_sense``   the typed word ties between senses, so each is searched and the row says so
``fuzzy``             one edit away, only for a concept that nothing else matched, labelled
====================  ==========================================================================

The card says how a result was found in fewer words than its route. The ways are in this order of
strength (:class:`FoundBy`):

1. The spelling: a name holds the word as typed, or the same quantity or date however written.
2. A Dictionary sense: a name holds a different spelling of the sense that the word names.
3. Starts with: a word in a name begins with the typed word. A plural or singular that the pool
   uses counts here too.
4. Plain name: the typed word stands inside a name, and not at a word's start. The search uses
   this only where none of the first three found anything.
5. A near spelling: the search uses this only where plain name found nothing either.

A route is how a stored word was reached. A way is what a person reads on the card.

A fuzzy match is never mixed silently with exact ones. The search offers it only where the concept
found nothing. A broader channel raises recall by importing unrelated neighbours. For example, a
latent semantic analysis model rated two unrelated stories as highly similar. A typo correction is
a broader channel, and the search shows it as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from ingence.dictionary.normalize import AtomKind

if TYPE_CHECKING:
    from ingence.dictionary.store import DictionaryStore
    from ingence.search.query import ParsedQuery, QueryTerm

__all__ = [
    "CLASS_HOW",
    "FOUND_BY_ORDER",
    "NEAR_HOW",
    "ROUTE_ORDER",
    "SENSE_HOW",
    "STEP_WORDS",
    "VARIANT_HOW",
    "Concept",
    "Expansion",
    "FoundBy",
    "Match",
    "MatchRoute",
    "Vocabulary",
    "expand",
    "route_step",
    "variants_of",
]


#: The kinds that a typed word is never read as a class of. The term of a connective or a question
#: word (``and``, ``does``) names no members that anybody searches for.
CLASS_NEVER_KINDS = frozenset({"connective", "question"})


class MatchRoute(StrEnum):
    """How a stored token came to match a typed word."""

    SENSE = "sense"
    TOKEN = "token"  # noqa: S105 (a match route, not a credential)
    QUANTITY = "quantity"
    DATE = "date"
    VARIANT = "variant"
    PIECE = "piece"
    STARTS_WITH = "starts_with"
    PLAIN_NAME = "plain_name"
    AMBIGUOUS_SENSE = "ambiguous_sense"
    FUZZY = "fuzzy"


class FoundBy(StrEnum):
    """How a result was found, as its card says it, with the strongest first."""

    SPELLING = "spelling"
    SENSE = "sense"
    STARTS_WITH = "starts_with"
    PLAIN_NAME = "plain_name"
    NEAR_SPELLING = "near_spelling"


#: The ways in order, strongest first. A unit's ``found_by`` in the ranking tuple is the weakest of
#: the strongest ways across the words it covers. The ranking compares ways by position here, as it
#: does for ``weakest_route``.
FOUND_BY_ORDER: tuple[FoundBy, ...] = (
    FoundBy.SPELLING,
    FoundBy.SENSE,
    FoundBy.STARTS_WITH,
    FoundBy.PLAIN_NAME,
    FoundBy.NEAR_SPELLING,
)

#: The routes in order, best first. A unit's ``weakest_route`` element in the ranking tuple is the
#: worst of the best routes across the concepts it covers. The ranking compares routes by position
#: here.
ROUTE_ORDER: tuple[MatchRoute, ...] = (
    MatchRoute.SENSE,
    MatchRoute.TOKEN,
    MatchRoute.QUANTITY,
    MatchRoute.DATE,
    MatchRoute.VARIANT,
    MatchRoute.PIECE,
    MatchRoute.AMBIGUOUS_SENSE,
    MatchRoute.STARTS_WITH,
    MatchRoute.PLAIN_NAME,
    MatchRoute.FUZZY,
)

#: How broadly a route reached the word, in four steps. The first step is the word itself: as
#: written, by its sense, as a plural or singular, or as a piece of a word kept whole. The next
#: steps are a word's start, then inside a name, then a near spelling. The ranking reads this step
#: before the kind of unit. So a word found only inside longer words never outranks the same word as
#: typed, and experiments still come first among units found by the word itself. The finer way
#: (:class:`FoundBy`) breaks ties later.
STEP_WORDS: tuple[str, ...] = ("word", "starts_with", "plain_name", "near_spelling")


def route_step(route: MatchRoute) -> int:
    """Return a route's step in :data:`STEP_WORDS`."""
    if route is MatchRoute.STARTS_WITH:
        return 1
    if route is MatchRoute.PLAIN_NAME:
        return 2
    if route is MatchRoute.FUZZY:
        return 3
    return 0


class Vocabulary(Protocol):
    """What a search needs to know about the words a pool uses. See ``projection``."""

    def entries_with(self, token: str) -> int:
        """Return the number of entries that carry this normalised token in their names."""
        ...

    def near(self, token: str, *, distance: int) -> tuple[str, ...]:
        """Return the tokens that the pool uses within ``distance`` edits, in canonical order."""
        ...

    def containing(self, piece: str) -> tuple[str, ...]:
        """Return the whole tokens that hold ``piece`` between case boundaries."""
        ...

    def holding(self, text: str) -> bool:
        """Return whether any token of the pool holds ``text`` anywhere in it, except itself."""
        ...

    def beginning(self, text: str) -> bool:
        """Return whether any token that the pool uses begins with ``text``, except itself."""
        ...


@dataclass(frozen=True, slots=True)
class Match:
    """One way a stored component may satisfy a concept.

    A quantity and a date match on their normalised form. The normaliser exists to make ``500nM``,
    ``500 nM`` and ``0.5uM`` one string. The census stored that string, and the query is read to the
    same one. Only a date typed shorter than a whole date needs more, and it says so with
    ``prefix``.
    """

    route: MatchRoute
    #: A normalised token to match on ``terms.token``, or None.
    token: str | None = None
    #: True where ``token`` is a prefix a stored date must start with (``2026-09``).
    prefix: bool = False
    #: A sense to match on ``terms.term_id``, or None.
    term_id: str | None = None
    #: The sense's readable name, for the explanation.
    term: str | None = None


@dataclass(frozen=True, slots=True)
class Expansion:
    """One typed word widened to other spellings, and how, in words.

    A question's answer lists these beside its words. They are a Dictionary sense's other
    spellings, a class word's members, a plural or singular that the file list uses, or a near
    spelling.
    """

    word: str
    to: tuple[str, ...]
    how: str


#: How an expansion is said, by the route that made it.
CLASS_HOW = "a class word: the words it stands for are searched"
SENSE_HOW = "another spelling of the same Dictionary sense"
VARIANT_HOW = "a plural or singular the file list uses"
NEAR_HOW = "a near spelling, offered because nothing matched as written"


@dataclass(frozen=True, slots=True)
class Concept:
    """One thing the person asked about, and every way the pool may say it."""

    index: int
    typed: str
    kind: str
    matches: tuple[Match, ...]
    excluded: bool = False
    #: The typed word as the tokeniser normalised it. "The spelling" way compares a name's word with
    #: it.
    token: str = ""
    #: The spellings offered for "did you mean", where nothing but a near spelling matched.
    suggestions: tuple[str, ...] = ()
    #: Plain words for the page: "FRAP names photobleaching_recovery (technique)".
    reading: str = ""
    #: Each widening of the typed word, labelled.
    expansions: tuple[Expansion, ...] = ()
    #: Whether anything knows the word. The file list spells it, a sense binds it, or a near
    #: spelling exists. False means "unknown" in a question's reading.
    known: bool = True


def variants_of(token: str) -> tuple[str, ...]:
    """Return the singular and plural spellings of an English word, conservatively.

    The function returns spellings only and never stems: ``plates`` and ``plate``, ``assays`` and
    ``assay``, ``replicates`` and ``replicate``, ``dishes`` and ``dish``. A stemmer would also make
    ``cells`` and ``cellular`` one word. A test of stemming on scientific prose found that a
    Porter-stemmed representation changed same-source recovery not at all. So stemming gains nothing
    that would pay for a search that reads ``cellulose`` for ``cells``. The function leaves words
    under four letters alone, because ``ns`` and ``ms`` are units and not plurals. The search uses a
    variant only where the pool itself spells it.
    """
    word = token.casefold()
    if len(word) < 4 or not word.isalpha():
        return ()
    found: list[str] = []
    if word.endswith("ies") and len(word) > 4:
        found.append(word[:-3] + "y")
    elif word.endswith(("ses", "xes", "zes", "ches", "shes")):
        found.append(word[:-2])
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        found.append(word[:-1])
    else:
        if word.endswith("y") and word[-2:-1] not in "aeiou":
            found.append(word[:-1] + "ies")
        elif word.endswith(("s", "x", "z", "ch", "sh")):
            found.append(word + "es")
        else:
            found.append(word + "s")
    return tuple(sorted(set(found) - {word}))


def _reading(term: QueryTerm, matches: list[Match]) -> str:
    senses = sorted({(match.term or "") for match in matches if match.term})
    if not senses:
        return f"{term.text!r} is searched as written"
    if len(senses) == 1:
        return f"{term.text!r} names {senses[0]}, and every spelling of it is searched"
    joined = ", ".join(senses)
    return f"{term.text!r} could name {joined}; each is searched and the results say which"


def _concept(
    term: QueryTerm,
    *,
    store: DictionaryStore,
    vocabulary: Vocabulary,
    fuzzy_min_length: int,
    fuzzy_distance: int,
    piece_min_length: int,
    piece_limit: int,
    inside_min_length: int,
) -> Concept:
    atom = term.atom
    matches: list[Match] = []
    expansions: list[Expansion] = []
    typed = atom.normalised

    if atom.kind is AtomKind.QUANTITY:
        matches.append(Match(route=MatchRoute.QUANTITY, token=typed))
    elif atom.kind is AtomKind.DATE and atom.ambiguous and atom.alternatives:
        # A date typed with day and month swapped is read both ways, as a typed day is.
        for reading in atom.alternatives:
            matches.append(Match(route=MatchRoute.DATE, token=reading, prefix=True))
    elif atom.kind is AtomKind.DATE:
        matches.append(Match(route=MatchRoute.DATE, token=typed, prefix=True))
    else:
        hit = store.look_up(atom.text)
        named_senses: list[str] = []
        if hit is not None:
            route = MatchRoute.AMBIGUOUS_SENSE if hit.ambiguous else MatchRoute.SENSE
            for sense in (hit.sense, *hit.alternatives):
                matches.append(Match(route=route, term_id=store.term_id(sense), term=sense.term))
                named_senses.append(sense.term)
                others = tuple(surface for surface in sense.surfaces if surface.casefold() != typed)
                if others:
                    expansions.append(Expansion(word=term.text, to=others, how=SENSE_HOW))
        # A class word names its members. A word typed as a sense's term and not as any surface of
        # it is searched as every member, through the Dictionary's own listing of the sense. An
        # example is ``nuclear stain`` for ``dapi`` and ``hoechst``, or ``nucleotide`` for ``atp``
        # and ``adp``. The widening is labelled as a class word's.
        class_sense = store.sense_for_term(typed)
        if (
            class_sense is not None
            and class_sense.term not in named_senses
            and class_sense.theme_kind not in CLASS_NEVER_KINDS
        ):
            matches.append(
                Match(
                    route=MatchRoute.SENSE,
                    term_id=store.term_id(class_sense),
                    term=class_sense.term,
                )
            )
            expansions.append(
                Expansion(word=term.text, to=tuple(class_sense.surfaces), how=CLASS_HOW)
            )
        matches.append(Match(route=MatchRoute.TOKEN, token=typed))
        variants = tuple(
            variant for variant in variants_of(typed) if vocabulary.entries_with(variant) > 0
        )
        for variant in variants:
            matches.append(Match(route=MatchRoute.VARIANT, token=variant))
        if variants:
            expansions.append(Expansion(word=term.text, to=variants, how=VARIANT_HOW))
        # A piece of a word kept whole. The binder reads ``eSubX`` as one word, and splitting a word
        # at a capital would be wrong. A person who searches ``SubX`` still means it. So the pieces
        # are indexed for search only and never bound, and a match through one says so. A piece has
        # at least three letters, the tokeniser's own case-piece minimum.
        if atom.kind is AtomKind.WORD and len(typed) >= piece_min_length:
            for holder in vocabulary.containing(typed)[:piece_limit]:
                if holder != typed:
                    matches.append(Match(route=MatchRoute.PIECE, token=holder))
        # A word beginning with the typed word. The SQL matches it against the names' words, so a
        # cap leaves none out. This applies only to a word that the person wants, because an
        # excluded word excludes as written and never by a piece of a name. It applies only from
        # ``inside_min_length`` letters, because below that length every name holds the word.
        if atom.kind is AtomKind.WORD and not term.excluded and len(typed) >= inside_min_length:
            matches.append(Match(route=MatchRoute.STARTS_WITH, token=typed))

    # Whether the word as written, a sense, a variant, a piece or a word's start found anything.
    found_named = (
        any(vocabulary.entries_with(match.token) > 0 for match in matches if match.token)
        or any(match.token is None for match in matches)
        or any(
            match.route is MatchRoute.STARTS_WITH and vocabulary.beginning(typed)
            for match in matches
        )
    )
    # A name that holds the word anywhere counts only where nothing above found it, as a near
    # spelling does. A word inside longer words is a broader channel, and a broader channel imports
    # unrelated neighbours, such as ``excellent`` for ``cell``.
    holding_it = False
    if (
        not found_named
        and atom.kind is AtomKind.WORD
        and not term.excluded
        and len(typed) >= inside_min_length
    ):
        matches.append(Match(route=MatchRoute.PLAIN_NAME, token=typed))
        holding_it = vocabulary.holding(typed)

    suggestions: tuple[str, ...] = ()
    found_anything = found_named or holding_it
    if not found_anything and atom.kind is AtomKind.WORD and len(typed) >= fuzzy_min_length:
        suggestions = vocabulary.near(typed, distance=fuzzy_distance)
        matches.extend(Match(route=MatchRoute.FUZZY, token=near) for near in suggestions)
        if suggestions:
            expansions.append(Expansion(word=term.text, to=suggestions, how=NEAR_HOW))

    return Concept(
        index=term.concept,
        typed=term.text,
        kind=atom.kind.value,
        matches=tuple(matches),
        excluded=term.excluded,
        token=typed,
        suggestions=suggestions,
        reading=_reading(term, matches),
        expansions=tuple(expansions),
        known=found_anything or bool(suggestions),
    )


def expand(
    parsed: ParsedQuery,
    *,
    store: DictionaryStore,
    vocabulary: Vocabulary,
    fuzzy_min_length: int = 5,
    fuzzy_distance: int = 1,
    piece_min_length: int = 3,
    piece_limit: int = 24,
    inside_min_length: int = 4,
) -> tuple[Concept, ...]:
    """Return one concept per typed concept, each with every match route it admits.

    ``fuzzy_min_length`` and ``fuzzy_distance`` are parameters and not constants inside the
    function, so a run can state them. Their defaults are similar to those of Spotlight, and they
    have not been measured on a real pool's vocabulary. A five-letter token often sits one edit from
    another, as every ``rep1``/``rep2`` pair does. That is why the search never corrects numbers or
    indexed atoms.

    ``inside_min_length`` is the shortest typed word that the search matches where a name's word
    begins with it or a name holds it anywhere. The default is four letters. The comment on the
    ``search`` setting gives the measured cost of three letters, the tokeniser's own case-piece
    minimum.
    """
    by_concept: dict[int, QueryTerm] = {}
    for term in parsed.terms:
        by_concept.setdefault(term.concept, term)
    return tuple(
        _concept(
            by_concept[index],
            store=store,
            vocabulary=vocabulary,
            fuzzy_min_length=fuzzy_min_length,
            fuzzy_distance=fuzzy_distance,
            piece_min_length=piece_min_length,
            piece_limit=piece_limit,
            inside_min_length=inside_min_length,
        )
        for index in sorted(by_concept)
    )
