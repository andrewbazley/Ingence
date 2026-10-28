"""Read a note's words as the engine reads a name: the terms it holds, and the words it does not.

As a person types a note on a Questions page, the page asks this route to read it. Only the
engine's own name reader reads it, and no model does. The reader is the census's normalizer and
tokenizer, with the years and the day the run's names were read on (``search._reader``, the reader
that reads a search query), and the binder's own lookup (``dictionary/discover._look_up``, which
gives the restrictions that a name's atoms are bound under). A person can type whatever they want.
Each term the engine recognizes becomes a chip, and a click on a chip adds the term to the file or
group that the note is about.

The response holds two lists, each for a chip beside the note:

- Each term the Dictionary binds: the words as typed, the sense and its axis, and a short
  sentence saying what it is read as ("brisa: a sample").
- Each word that nothing binds and that names in the run carry, with how many names carry it. Only
  such a word can be given a meaning through the Record's word target (``decide.define_word``
  refuses the rest), and an ordinary word a person types is not a word of the data. The route
  counts the other words. It leaves out function words (``textword/prose.DEFAULT_STOPWORDS``)
  before any of this.

Reading writes nothing. A chip writes only when a person clicks it, through the Record's one way
in (``POST /api/review/answer``). A term is written as an observation about the thing being asked
about (``record/verdicts.write_observation``). A word that nothing binds is written as a meaning
for that word, the same write that a word question's ``sense [axis]`` answer makes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ingence.core.ids import PROJECT_SCOPE
from ingence.dictionary.discover import _look_up
from ingence.dictionary.normalize import AtomKind
from ingence.textword.prose import DEFAULT_STOPWORDS

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = ["NOTE_LIMIT", "WORDS_READ", "read_note"]

#: The longest note read, in characters: a note is a sentence or two, and a page sends it on
#: every pause in typing. Longer text is read up to here and said so.
NOTE_LIMIT = 2_000

#: The most chips of each kind a note returns: enough for any note a person writes beside a row.
WORDS_READ = 40

#: The kinds of atom a note's chips can name: words and labels, never a number or a date, which
#: a person's note holds as plain facts and the engine reads as values rather than terms.
_READ_KINDS = frozenset({AtomKind.WORD, AtomKind.INDEXED, AtomKind.IDENTIFIER})


def _spoken_kind(theme_kind: str) -> str:
    spoken = theme_kind.replace("_", " ").strip() or "term"
    return f"{'an' if spoken[:1].lower() in 'aeiou' else 'a'} {spoken}"


def read_note(catalog: Catalog, project: Path, text: str | None) -> dict[str, Any]:
    """Return the terms a note holds and the words it holds that nothing binds; write nothing."""
    from ingence.api.routes.search import _reader

    said = (text or "")[:NOTE_LIMIT]
    if not said.strip():
        return {"text": "", "terms": [], "unbound": [], "unbound_elsewhere": 0, "cut": False}
    normaliser, store = _reader(catalog, project)
    terms: dict[str, dict[str, Any]] = {}
    unbound: dict[str, dict[str, Any]] = {}
    for atom in normaliser.atoms(said):
        if atom.kind not in _READ_KINDS:
            continue
        folded = atom.text.casefold()
        # A function word is no term of the data, whatever the Dictionary makes of it ("from").
        if folded in terms or folded in unbound or folded in DEFAULT_STOPWORDS:
            continue
        hit, _ = _look_up(atom, store=store, scope=PROJECT_SCOPE)
        if hit is not None and hit.sense is not None:
            if len(terms) >= WORDS_READ:
                continue
            sense = hit.sense
            differs = sense.term.replace("_", " ").casefold() != folded
            terms[folded] = {
                "text": atom.text,
                "term": sense.term,
                "theme_kind": sense.theme_kind,
                "tied": bool(hit.alternatives),
                "said": f"{atom.text}: {_spoken_kind(sense.theme_kind)}"
                + (f" ({sense.term.replace('_', ' ')})" if differs else ""),
            }
            continue
        if atom.kind is not AtomKind.WORD or len(folded) < 2:
            continue
        if len(unbound) < WORDS_READ:
            unbound[folded] = {"word": atom.text, "files": 0}
    carried: dict[str, int] = {}
    if unbound and catalog.has_run:
        carried = {
            str(word): int(files)
            for word, files in catalog.query(
                "SELECT lower(normalized_value), count(DISTINCT entry_id) FROM components "
                "WHERE lower(normalized_value) IN (SELECT unnest(?::VARCHAR[])) GROUP BY 1",
                [sorted(unbound)],
            ).fetchall()
        }
    # A word that nothing binds is a chip only where names in this run carry it. That is where a
    # meaning can be given (``define_word`` refuses a word that no name carries). An ordinary word
    # a person types ("look", "like") is not a word of the data. The route counts the rest.
    named = [
        {**word, "files": carried[folded]} for folded, word in unbound.items() if folded in carried
    ]
    from ingence.api.routes.review import _theme_kinds

    return {
        "text": said,
        "terms": list(terms.values()),
        "unbound": named,
        "unbound_elsewhere": len(unbound) - len(named),
        "cut": len(text or "") > NOTE_LIMIT,
        # The axes a meaning may be given on, for a word nothing binds (the Review view's own).
        "theme_kinds": _theme_kinds(catalog) if catalog.has_run else [],
    }
