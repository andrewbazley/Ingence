"""Choose the word the lattice sees for a spelling: its Dictionary term, or the spelling.

The groups are made of words. An experiment's terms are the words that its folder's name and its
acquisitions' names hold, and a group is a set of terms that experiments share. If the lattice kept
each word as a spelling, a person who teaches the engine that ``widefield`` and ``epifluorescence``
are one thing could not sharpen a group, because two experiments written two ways would share
nothing. So a word that the Dictionary binds enters the lattice as its Dictionary term, and the
spellings a person called one thing are one word to the lattice.

The setting ``groups.words_as`` is ``terms`` (the default) or ``spellings`` (the lattice keeps each
spelling). A word that no Dictionary sense holds, or that two senses claim level, is never a term.
It stays its spelling either way, because there is nothing to read it as.

:func:`lattice_word` is the one function that turns a spelling and the term it is bound to (or
none) into the lattice's word. The lattice's terms (``rulebook/predicates/experiments.py``), the
stored ``group:`` answers (:meth:`WordMap.answer`) and the topics all read it, so no two of them
can disagree about which word a spelling is.

Stored approvals survive a switch of the setting. A ``group:<words>`` answer is written in whatever
words the lattice held when the person answered. :meth:`WordMap.answer` reads one in the words the
lattice holds now. Under ``terms``, each bound spelling in it becomes its term, and an answer that
is already in terms is unchanged. Under ``spellings``, a word the run writes at all, bound or not,
stays as it is. Each term that the run never writes becomes the spellings of that term that the
run's names write, one answer for each way of choosing them (:data:`EXPANSION_LIMIT` bounds the
ways).

Groups still show a word as the files wrote it (``dictionary/written.py``). A core keyed by a term
is shown with the spelling that most of the run's files hold, and never in the Dictionary's name
for the sense. Nothing here is shown. This module only chooses the key.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ingence.storage.catalog import Catalog

__all__ = [
    "EXPANSION_LIMIT",
    "KEPT_RUNS",
    "RULES_BEFORE_TERMS",
    "SPELLINGS",
    "TERMS",
    "WORDS_AS",
    "WORDS_AS_FILE",
    "WordMap",
    "carry_reading",
    "lattice_word",
    "reading_in",
    "run_words_as",
    "words_said_by_run",
    "write_reading",
]

#: The two readings of a bound word: its Dictionary term, or the spelling the name wrote.
TERMS = "terms"
SPELLINGS = "spellings"
WORDS_AS: tuple[str, ...] = (TERMS, SPELLINGS)

#: The most ways a stored answer in terms is spelled out when the lattice reads spellings: a term
#: the run's names write three ways, in an answer of two such terms, is nine. A granularity bound,
#: stated where it cuts (an answer with more ways is read as its first ways in canonical order).
EXPANSION_LIMIT = 16


#: The maps built for published runs, by run and reading, oldest dropped first.
_KEPT: dict[str, WordMap] = {}
_KEPT_LOCK = threading.Lock()
#: How many runs' maps are kept: as many as the page's other per-run readings (four).
KEPT_RUNS = 4


#: The file in a run's directory that says which words the run made its groups of. Inference writes
#: it, and a dive, which carries inference's claims into its own run, carries it with them. It is
#: a file of its own. It is not a column of the run's record, because a change to a stored shape
#: needs a schema version. It is not a row of the journal, because the journal is the
#: development log, which a stable build switches off, and a dive writes the journal afresh.
WORDS_AS_FILE = "lattice_words.json"

#: The versions of the rules that read the lattice's words before a bound word was read as its
#: term: a run whose rules table holds one of these rules at this version or lower was made by an
#: engine that read spellings. Each rule moved to the next version when terms came
#: (``rulebook/revision_4.py``). A rule's version may carry a suffix (``2+d1``), and only its
#: number counts.
RULES_BEFORE_TERMS: dict[str, int] = {
    "experiments_may_belong_together": 3,
    "group_resolved_within_experiment": 2,
    "group_resolved_across_experiments": 2,
    "group_resolution_deviation": 2,
}


def write_reading(directory: Path, words_as: str) -> None:
    """Say, in a run's directory, which words the run made its groups of."""
    (directory / WORDS_AS_FILE).write_text(
        json.dumps({"words_as": words_as}, sort_keys=True), encoding="utf-8"
    )


def _made_before_terms(directory: Path) -> bool:
    """Whether the run in this directory was made by an engine that read spellings only.

    Decided by the run's own rules (its rules table travels with its claims on every path that
    carries them), not by the package's version: a rule that read the lattice's words, at a version
    before the one that reads terms, says the claims were made of spellings. A run with none of
    these rules, or with them at the version that reads terms, says nothing of the sort.
    """
    import pyarrow.parquet as pq

    parts = sorted((directory / "rules").glob("*.parquet"))
    try:
        if not parts:
            return False
        names = pq.read_schema(parts[0]).names
        if "rule_id" not in names or "version" not in names:
            return False
        rows = pq.read_table(parts, columns=["rule_id", "version"]).to_pylist()
    except (OSError, ValueError):
        return False
    for row in rows:
        before = RULES_BEFORE_TERMS.get(str(row.get("rule_id")))
        number = re.match(r"\d+", str(row.get("version") or "").strip())
        if before is not None and number is not None and int(number.group()) <= before:
            return True
    return False


def reading_in(directory: Path | None) -> str | None:
    """Which words the run in ``directory`` made its groups of, or None where it cannot be told.

    The file the run states it in (:data:`WORDS_AS_FILE`) first. A run without it is read as
    spellings only where its own rules are those of an engine before terms
    (:data:`RULES_BEFORE_TERMS`); a run whose rules read terms, made without the file, is not
    guessed at.
    """
    if directory is None or not directory.is_dir():
        return None
    try:
        said = json.loads((directory / WORDS_AS_FILE).read_text(encoding="utf-8")).get("words_as")
    except (OSError, ValueError, AttributeError):
        said = None
    if said in WORDS_AS:
        return str(said)
    return SPELLINGS if _made_before_terms(directory) else None


def carry_reading(source: Path, destination: Path) -> None:
    """Say in ``destination`` what the run in ``source`` made its groups of, for a run that carries
    that run's claims forward (a dive). Says nothing where the source cannot be told: the new run
    then cannot be told either, and the pages say so."""
    said = reading_in(source)
    if said is not None:
        write_reading(destination, said)


def words_said_by_run(catalog: object) -> str | None:
    """The ``groups.words_as`` a published run was bound by, or None where it cannot be told.

    Read from the run's own directory (:func:`reading_in`), never from the settings file as it
    stands now: the groups a page shows are the run's, in the words the run asked them in, until a
    run has read a change of the setting. Anything that is no run's catalog reads spellings.
    """
    if not getattr(catalog, "has_run", False):
        return SPELLINGS
    directory = getattr(catalog, "run_directory", None)
    return reading_in(Path(str(directory))) if directory is not None else SPELLINGS


def run_words_as(catalog: object) -> str:
    """The ``groups.words_as`` a published run was bound by: ``terms`` or ``spellings``.

    For a reader that needs a value and shows nothing it must get right (a spelling's display, a
    search): a run that cannot be told reads as spellings there. The lattice and resolutions pages
    use :func:`words_said_by_run`, and say so in words where it is None.
    """
    return words_said_by_run(catalog) or SPELLINGS


def lattice_word(spelling: str, term: str | None, *, words_as: str = TERMS) -> str:
    """The lattice's word for one spelling: its term where it is bound and terms are read.

    ``spelling`` is the word as the census stores it (a component's normalized value) and
    ``term`` the Dictionary term of the sense that binds it in this name, or None where no sense
    does (unknown, contested, or never looked up). The word is casefolded either way, as every
    group word is (``group_target``).
    """
    if words_as == TERMS and term:
        return term.casefold()
    return spelling.casefold()


class WordMap:
    """The terms a run's spellings are bound to, and a stored answer read in the lattice's words.

    Built once per run from ``(spelling, term, components)`` rows: how many word components with a
    resolved binding write the spelling bound to the term (:meth:`from_pairs` counts one each).
    A spelling bound to more than one term across the run (a folder's own meaning of it) maps to
    the one most components hold, ties going to the term that sorts first. The lattice itself
    reads each name's own binding (:func:`lattice_word`), so this map is only for reading what a
    person wrote, which is not tied to any one name.
    """

    def __init__(
        self,
        rows: Iterable[tuple[str, str, int]],
        *,
        words_as: str = TERMS,
        written: Iterable[str] = (),
    ) -> None:
        if words_as not in WORDS_AS:
            message = f"no lattice reading {words_as!r}; the readings are {', '.join(WORDS_AS)}"
            raise ValueError(message)
        self.words_as = words_as
        counted: dict[str, Counter[str]] = defaultdict(Counter)
        for spelling, term, components in rows:
            folded, named = spelling.strip().casefold(), term.strip().casefold()
            if folded and named:
                counted[folded][named] += components
        self._term_of: dict[str, str] = {
            spelling: min(found, key=lambda term: (-found[term], term))
            for spelling, found in counted.items()
        }
        #: Every word the run writes, bound or not: a stored answer's word the run writes at
        #: all stays as it is under ``spellings``, whatever binds it or does not.
        self._written: frozenset[str] = frozenset(
            {*self._term_of, *(word.strip().casefold() for word in written if word.strip())}
        )
        spelled: dict[str, set[str]] = defaultdict(set)
        for spelling, term in self._term_of.items():
            spelled[term].add(spelling)
        #: Every spelling of each term the run's names write, sorted.
        self._spellings_of: dict[str, tuple[str, ...]] = {
            term: tuple(sorted(found)) for term, found in spelled.items()
        }

    @classmethod
    def from_pairs(
        cls,
        pairs: Iterable[tuple[str, str]],
        *,
        words_as: str = TERMS,
        written: Iterable[str] = (),
    ) -> WordMap:
        """A map built from one ``(spelling, term)`` pair for each bound word component, and every
        word the run writes (``written``), bound or not."""
        return cls(
            ((spelling, term, 1) for spelling, term in pairs), words_as=words_as, written=written
        )

    def word(self, spelling: str) -> str:
        """The lattice's word for a spelling the run holds, by the run-wide map."""
        folded = spelling.strip().casefold()
        return lattice_word(folded, self._term_of.get(folded), words_as=self.words_as)

    def answer(self, words: Iterable[str]) -> list[tuple[str, ...]]:
        """A stored ``group:`` answer's words, read in the words the lattice holds now.

        Each way of reading it is a sorted tuple of distinct words. Under ``terms`` there is one:
        a bound spelling becomes its term, anything else stays. Under ``spellings`` a word the run
        writes at all, bound or not, stays exactly as it is, and a term the run never writes is
        spelled out as the ways it is written, one answer for each choice, up to
        :data:`EXPANSION_LIMIT`.
        """
        folded = sorted({word.strip().casefold() for word in words if word.strip()})
        if self.words_as == TERMS:
            return [tuple(sorted({self._term_of.get(word, word) for word in folded}))]
        choices: list[Sequence[str]] = []
        for word in folded:
            # A word the run writes stays exactly itself, bound or not, whatever term it is
            # the name of (a term spelled by its own name and by a short form is one such word, and
            # a term's own name the run writes but leaves unbound, being contested, is another):
            # reading it as every spelling of its term would approve cores nobody approved. Only a
            # term the run never writes at all is spelled out, as the ways the run writes it.
            spelled = self._spellings_of.get(word, ())
            choices.append((word,) if word in self._written or not spelled else spelled)
        found: list[tuple[str, ...]] = []
        for chosen in product(*choices):
            reading = tuple(sorted(set(chosen)))
            if reading not in found:
                found.append(reading)
            if len(found) >= EXPANSION_LIMIT:
                break
        return found

    def spellings(self, term: str) -> tuple[str, ...]:
        """The spellings of one term the run's names write, sorted; empty for an unbound word."""
        return self._spellings_of.get(term.strip().casefold(), ())

    @classmethod
    def of_run(cls, catalog: Catalog, *, words_as: str = TERMS) -> WordMap:
        """The map of a published run: its word components with a resolved binding, by the term.

        Read from the run's own tables, counted by components (``dictionary_bindings`` joined to
        ``components``); a run without bindings maps nothing, so every word stays its spelling.
        """
        # A stand-in catalog that is no published run has neither, and keeps nothing.
        directory = (
            getattr(catalog, "run_directory", None) if getattr(catalog, "has_run", False) else None
        )
        key = f"{directory}|{words_as}" if directory else ""
        with _KEPT_LOCK:
            kept = _KEPT.get(key) if key else None
        if kept is not None:
            return kept
        try:
            found = catalog.query(
                """
                SELECT lower(c.normalized_value), lower(b.term), count(*)
                FROM components c JOIN dictionary_bindings b ON b.component_id = c.component_id
                WHERE c.component_type = 'word' AND c.source_scope = 'name'
                  AND b.resolution_status = 'resolved' AND b.term IS NOT NULL
                GROUP BY 1, 2
                """
            ).fetchall()
        except Exception:  # a run without bindings: every word stays its spelling
            found = []
        try:
            written = [
                str(word)
                for (word,) in catalog.query(
                    "SELECT DISTINCT lower(normalized_value) FROM components "
                    "WHERE component_type = 'word' AND source_scope = 'name'"
                ).fetchall()
            ]
        except Exception:  # a stand-in that holds no components
            written = []
        built = cls(
            ((str(a), str(t), int(n)) for a, t, n in found), words_as=words_as, written=written
        )
        if key:
            # A published run never changes, so its map is kept (the few runs a page moves between).
            with _KEPT_LOCK:
                _KEPT[key] = built
                while len(_KEPT) > KEPT_RUNS:
                    del _KEPT[next(iter(_KEPT))]
        return built

    def __bool__(self) -> bool:
        return bool(self._term_of)

    def terms(self) -> Mapping[str, str]:
        """Every bound spelling to its term (a copy), for a reader that wants the whole table."""
        return dict(self._term_of)
