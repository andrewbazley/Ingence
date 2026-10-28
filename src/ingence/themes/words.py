"""The words of a name as a theme reads them.

A word that the Dictionary binds is its sense, ``§term`` (every spelling of one sense merges),
unless the sense's kind says how a file was made (``ThemeSettings.how_kinds``), when the word is
left out. A word that the Dictionary does not bind is read as its stems. It is kept whole where it
is a construct or a compound number (``kelt12``). Otherwise it is split at its numbers into the
pieces of two or more letters, and a piece that the Dictionary binds to a "how" kind is left out
(``zeph500nm`` gives ``zeph``; ``nm`` is a unit). No word list lives here: the Dictionary's kinds
and the settings decide what is left out.

One word rule (:func:`word_tokens`) reads a run's names (the projection) and a free text
(:func:`read_text`, :func:`read_texts`: a title, a description, keywords). So an archive's study
texts enter the themes as a run's experiments do. The engine's own name reader reads each line
(``ingence.dictionary.discover.read_name``), this rule reads its pieces, and the words of prose
that name nothing (``ingence.textword.prose.DEFAULT_STOPWORDS``) are left out where the Dictionary
binds nothing.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from ingence.core.settings import ThemeSettings

__all__ = [
    "SENSE_MARK",
    "TextCorpus",
    "TextWords",
    "how_piece",
    "plural_folds",
    "read_text",
    "read_texts",
    "series_prefixes",
    "stems",
    "token_display",
    "word_tokens",
]

#: What marks a token as a sense rather than a spelling.
SENSE_MARK = "§"
#: A construct or compound number, kept whole: letters, digits, and once more (``kelt12b3``).
_COMPOUND = re.compile(r"^[a-z]+\d+(?:[a-z]\d+)?$")
#: A number, with its decimal part.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
#: A series member: letters then one to three digits.
_SERIES = re.compile(r"^([a-z]+)(\d{1,3})$")
#: How many different numbers a prefix needs before its members read as one series.
SERIES_MEMBERS = 3


def stems(value: str, how: Callable[[str], bool] = lambda _: False) -> list[str]:
    """A word's stems, deduplicated in order: the construct whole, or its pieces between numbers."""
    word = value.strip().casefold()
    if not word:
        return []
    found: list[str] = []
    if _COMPOUND.match(word):
        found.append(word)
    for piece in _NUMBER.split(word):
        piece = piece.strip("_-. ")
        if len(piece) >= 2 and not piece.isdigit() and not how(piece):
            found.append(piece)
    return list(dict.fromkeys(found))


def series_prefixes(tokens: Iterable[str]) -> set[str]:
    """The prefixes written with three or more different numbers (``vorn1, vorn2, vorn7``)."""
    numbers: dict[str, set[str]] = defaultdict(set)
    for token in tokens:
        matched = _SERIES.match(token)
        if matched:
            numbers[matched.group(1)].add(matched.group(2))
    return {prefix for prefix, seen in numbers.items() if len(seen) >= SERIES_MEMBERS}


def in_series(token: str, prefixes: set[str]) -> str:
    """The token, or its prefix where it is a member of a series."""
    matched = _SERIES.match(token)
    return matched.group(1) if matched and matched.group(1) in prefixes else token


#: What a singular ends in before its plural may add "es" (``brusches``, ``glabboxes``,
#: ``drovoes``). After any other letter "es" is an "e" and an "s" (``spirnes`` is ``spirne``'s).
ES_AFTER: tuple[str, ...] = ("s", "x", "z", "ch", "sh", "o")
#: Endings that are not a regular plural's (``glass``, ``status``, ``analysis``).
NOT_PLURAL: tuple[str, ...] = ("ss", "us", "is")
#: The shortest singular a plural is folded onto (two letters are an abbreviation's).
PLURAL_MIN_SINGULAR = 3


def _singulars(word: str) -> list[str]:
    """The singulars a word may be the regular plural of, the longest first: ``s``, then ``es``
    after a hissing sound or ``o``, then ``ies`` to ``y``."""
    found: list[str] = []
    if word.endswith("s"):
        found.append(word[:-1])
    if word.endswith("es") and word[:-2].endswith(ES_AFTER):
        found.append(word[:-2])
    if word.endswith("ies"):
        found.append(word[:-3] + "y")
    return [singular for singular in found if len(singular) >= PLURAL_MIN_SINGULAR]


def _plurals(word: str) -> list[str]:
    """A word's own regular plurals (the converse of :func:`_singulars`)."""
    found = [f"{word}s"]
    if word.endswith(ES_AFTER):
        found.append(f"{word}es")
    if word.endswith("y"):
        found.append(f"{word[:-1]}ies")
    return found


def plural_folds(words: Iterable[str], senses: Mapping[str, str]) -> dict[str, str]:
    """Each word read as its singular within a corpus: ``{plural: singular}``.

    The mapping holds a pair where a word and its regular plural (s; es after s, x, z, ch, sh or o;
    ies to y) are both among ``words`` and neither is a sense (``§term`` or a word ``senses``
    names). So a word that the Dictionary does not know and its plural are one word. A word whose
    own plural is present is a singular and is never folded, so a fold's target is never itself
    folded (no chains). The
    result is deterministic."""
    present = {
        word for word in words if word and not word.startswith(SENSE_MARK) and word not in senses
    }
    folds: dict[str, str] = {}
    for word in sorted(present):
        if word.endswith(NOT_PLURAL) or any(plural in present for plural in _plurals(word)):
            continue
        singular = next((s for s in _singulars(word) if s in present), None)
        if singular is not None:
            folds[word] = singular
    # A target always has its plural present, so it is never folded; said, not assumed.
    return {word: target for word, target in folds.items() if target not in folds}


def token_display(token: str, names: Mapping[str, str] | None = None) -> str:
    """A token as a person reads it: a sense's canonical name, spaces for underscores."""
    shown = (names or {}).get(token, token)
    return shown.removeprefix(SENSE_MARK).replace("_", " ").strip()


def how_piece(store: Any, how: frozenset[str]) -> Callable[[str], bool]:
    """Whether a piece of an unbound word is a word the Dictionary binds to a "how" kind."""
    cache: dict[str, bool] = {}

    def is_how(piece: str) -> bool:
        if piece not in cache:
            hit = store.look_up(piece) if store is not None else None
            cache[piece] = hit is not None and str(hit.sense.theme_kind) in how
        return cache[piece]

    return is_how


def word_tokens(
    value: str | None,
    status: str | None,
    term: str | None,
    kind: str | None,
    *,
    how: frozenset[str],
    is_how: Callable[[str], bool],
    ignored: frozenset[str] = frozenset(),
) -> list[str]:
    """The theme layer's one word rule for one piece of a name and its binding.

    A sense that the Dictionary resolved is ``§term`` unless its kind says how a file was made
    (then nothing). Anything else is its stems, less the spellings a person left out."""
    if status == "resolved" and term:
        return [] if str(kind) in how else [f"{SENSE_MARK}{term}"]
    return [piece for piece in stems(str(value or ""), is_how) if piece not in ignored]


@dataclass(frozen=True)
class TextWords:
    """One free text's theme words, in order, and the senses among them by canonical name."""

    words: tuple[str, ...]
    senses: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TextCorpus:
    """A corpus of free texts ready for :func:`ingence.themes.core.compute_themes`."""

    #: ``{unit: words}``, series members read as their series (as a run's names are).
    documents: dict[str, list[str]]
    #: ``{§term: canonical name}`` for every sense read.
    senses: dict[str, str]
    #: The Dictionary's spellings written in parts, ``(part, part, ...) -> §term``.
    phrases: dict[tuple[str, ...], str]


#: The pieces of a name a theme reads (the projection's components of these types).
_READ_KINDS = ("word", "indexed")
#: What divides a spelling into its parts, for the phrases a corpus is given.
_PARTS = re.compile(r"[ _.\-]+")


class _Reader:
    """The engine's name reader and the word rule, set up once for many texts."""

    def __init__(
        self,
        store: Any,
        normaliser: Any,
        settings: ThemeSettings | None,
        stopwords: frozenset[str] | None,
    ) -> None:
        from ingence.core.settings import ThemeSettings
        from ingence.dictionary.normalize import Normaliser
        from ingence.dictionary.store import DictionaryStore
        from ingence.textword.prose import DEFAULT_STOPWORDS

        self.settings = settings or ThemeSettings()
        self.store = store if store is not None else DictionaryStore.builtin()
        self.normaliser = normaliser if normaliser is not None else Normaliser(self.store)
        self.stopwords = DEFAULT_STOPWORDS if stopwords is None else stopwords
        self.how = frozenset(self.settings.how_kinds)
        self.ignored = frozenset(word.casefold() for word in self.settings.ignored_words)
        self.is_how = how_piece(self.store, self.how)
        self.canonical: dict[str, str] = {}

    def name_of(self, term: str) -> str:
        if term not in self.canonical:
            sense = self.store.sense_for_term(term)
            self.canonical[term] = (
                str(sense.canonical) if sense is not None and sense.canonical else term
            )
        return self.canonical[term]

    def line(self, line: str, senses: dict[str, str]) -> list[str]:
        from ingence.core.schema import Entry, EntryType
        from ingence.dictionary.discover import read_name

        line = line.strip()
        if not line:
            return []
        entry = Entry(
            entry_id="text",
            root_id="text",
            relative_path="text",
            name=line,
            entry_type=EntryType.DIRECTORY,
            first_seen_run="text",
            last_seen_run="text",
        )
        reading = read_name(
            entry, run_id="text", normaliser=self.normaliser, store=self.store, with_shape=False
        )
        bound = {b.component_id: b for b in reading.bindings}
        by_value = {
            (c.component_type, c.normalized_value): bound.get(c.component_id)
            for c in reading.components
        }
        found: list[str] = []
        for atom in self.normaliser.atoms(line):
            kind = atom.kind.value
            if kind not in _READ_KINDS:
                continue
            binding = by_value.get((kind, atom.normalised))
            status = binding.resolution_status.value if binding is not None else None
            term = binding.term if binding is not None else None
            theme_kind = str(binding.theme_kind) if binding is not None else None
            for token in word_tokens(
                atom.normalised,
                status,
                term,
                theme_kind,
                how=self.how,
                is_how=self.is_how,
                ignored=self.ignored,
            ):
                if token.startswith(SENSE_MARK):
                    senses[token] = self.name_of(token.removeprefix(SENSE_MARK))
                elif token in self.stopwords:
                    continue
                found.append(token)
        return found

    def fields(self, text: str | Sequence[str], senses: dict[str, str]) -> list[list[str]]:
        """Each field's words in order, every line of it read as one name; a field that is
        missing (None) or empty is skipped, never read as a word."""
        fields = [text] if isinstance(text, str) else list(text)
        return [
            [token for line in value.splitlines() for token in self.line(line, senses)]
            for value in fields
            if isinstance(value, str) and value.strip()
        ]

    def phrases(self) -> dict[tuple[str, ...], str]:
        found: dict[tuple[str, ...], str] = {}
        for sense in sorted(self.store.senses(), key=lambda s: str(s.term)):
            if str(sense.theme_kind) in self.how:
                continue
            for surface in sorted(sense.surfaces):
                parts = tuple(p for p in _PARTS.split(str(surface).casefold()) if p)
                if len(parts) > 1:
                    found.setdefault(parts, f"{SENSE_MARK}{sense.term}")
        return dict(sorted(found.items()))


def read_text(
    text: str | Sequence[str],
    *,
    store: Any = None,
    normaliser: Any = None,
    settings: ThemeSettings | None = None,
    stopwords: frozenset[str] | None = None,
) -> TextWords:
    """A free text's theme words, read as a run's names are.

    The function reads each line by the engine's name reader as one name and each piece by
    :func:`word_tokens`. It leaves out a word that the Dictionary does not bind where the word is
    in the prose stop list (``stopwords``, default ``ingence.textword.prose.DEFAULT_STOPWORDS``).
    ``store`` is the Dictionary (default the built-in one, which a new project loads), and
    ``normaliser`` is its normaliser (default the engine's). Each word counts once per field.
    ``text`` is one field, or several (a title, a description, a keywords field, each one
    element), and how a field is broken into lines changes nothing. Markup and web addresses are
    the caller's to remove. The result is deterministic."""
    reader = _Reader(store, normaliser, settings, stopwords)
    senses: dict[str, str] = {}
    found = [t for field in reader.fields(text, senses) for t in dict.fromkeys(field)]
    return TextWords(words=tuple(found), senses=dict(sorted(senses.items())))


def read_texts(
    texts: Mapping[str, str | Sequence[str]],
    *,
    store: Any = None,
    normaliser: Any = None,
    settings: ThemeSettings | None = None,
    stopwords: frozenset[str] | None = None,
) -> TextCorpus:
    """A corpus of free texts, ``{unit: text or fields}``, read by :func:`read_text`, with series
    members read as their series across the corpus (as a run's names are) and the Dictionary's
    spellings written in parts as phrases. Units are in canonical order."""
    reader = _Reader(store, normaliser, settings, stopwords)
    senses: dict[str, str] = {}
    raw = {unit: reader.fields(texts[unit], senses) for unit in sorted(texts)}
    prefixes = series_prefixes(
        t
        for fields in raw.values()
        for field in fields
        for t in field
        if not t.startswith(SENSE_MARK)
    )
    # Each word once per field, as a run's name gives each component once, wherever the field
    # is broken into lines.
    documents = {
        unit: [t for field in fields for t in dict.fromkeys(in_series(t, prefixes) for t in field)]
        for unit, fields in raw.items()
    }
    return TextCorpus(
        documents=documents, senses=dict(sorted(senses.items())), phrases=reader.phrases()
    )
