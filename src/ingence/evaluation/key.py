"""The answer key: what a person would know about a folder, written beside it and never inside it.

One format serves both halves. The generator writes a key for the pool it invents. A person writes
one for a real folder, by hand, for the laboratory runner to score. The engine never reads a key
while it indexes or infers. A key is what a person knows, and the answers are withheld from the
Index, as a listing's declared links are.

Paths are POSIX, relative to the folder the key describes, the way the Index writes an entry's
``relative_path``. A folder is named by its path without a trailing slash. A key describes one
root: two roots can each hold one relative path, so a project of several roots is scored one root
at a time (``--root``), and a key saved inside any root is refused, never read as the engine's.

Two kinds of family.

- A *value* family answers one question per path: which stage a file is at, whether a folder is an
  experiment, what date a name states. ``expected`` maps a path to its value, or to ``null`` where
  the answer is that there is none (a script has no stage; ``raw/`` is not an experiment).
- A *pair* family answers which paths belong together: replicates, copies, groups of experiments
  (undirected, written as ``groups`` or ``pairs``), references and derivations (directed, written
  as ``pairs``, the mentioning or the source file first).

Every family carries two more lists. ``undecidable`` names what the files alone cannot settle,
where the right output is abstention (an unknown is not a wrong). ``ignored`` names
what the key leaves out of the score, where any answer is acceptable. And ``complete`` says whether
the family answers every path it could (a generated key: every engine output is scored) or only the
paths it names (a person's key for a real folder: outputs about paths it never names are counted
apart as outside the key, never as wrong; a pair counts only where the key names both its paths).

Groups by their words. The group family may also carry ``named``: each name a term set, each
value the experiments a person puts under it, so the lattice a pool holds (five experiments sharing
``kelt vorn``, two of them also ``zib``) is written level by level, and the scorer counts each named
group the engine proposed whole beside the pairs it found.

A key may also carry a words family beside the others: what each spelling in the names means and
which spellings are one thing. It is not scored per path, so it sits beside ``families`` and not in
it. A key without one is still a valid key and is read and written unchanged. The sample
collection's vocabulary tier writes it (:mod:`ingence.evaluation.sample_lab_vocabulary`). The oracle
teacher in :mod:`ingence.evaluation.score` answers from it.

A person's key for a real folder, in the same format, with invented names::

    {
      "format": "ingence-answer-key",
      "format_version": 1,
      "describes": {"written_by": "a person", "minutes_spent": 20},
      "families": {
        "experiment_folder": {"complete": false,
                              "expected": {"2021 kelt vorn": "experiment",
                                           "2021 kelt vorn/raw": null}},
        "stage": {"complete": false,
                  "expected": {"2021 kelt vorn/raw/kelt_001.nd2": "raw",
                               "2021 kelt vorn/kelt_001_results.csv": "analysis"}},
        "group": {"complete": false, "groups": [["2021 kelt vorn", "2022 kelt vorn zib"]]},
        "copy": {"complete": false, "groups": [["brisa/table.csv", "brisa backup/table.csv"]]}
      }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
    from pathlib import Path

__all__ = [
    "FAMILIES",
    "KEY_FORMAT",
    "KEY_FORMAT_VERSION",
    "PAIR_FAMILIES",
    "VALUE_FAMILIES",
    "AnswerKey",
    "Family",
    "PairFamily",
    "Question",
    "QuestionsFamily",
    "ValueFamily",
    "WordMeaning",
    "WordsFamily",
    "load_key",
]

KEY_FORMAT = "ingence-answer-key"
KEY_FORMAT_VERSION = 1

#: The families a value answers, one value per path, and what each value may be.
VALUE_FAMILIES: dict[str, str] = {
    "stage": "raw, processed or analysis: where a file stands in the workflow",
    "experiment_folder": "experiment: the folder one experiment's files are kept in",
    "program": "program: the outermost folder of a program bundle, beneath which rules defer",
    "empty_file": "empty_output beside siblings with content, or empty on its own",
    "contradicted_extension": "the media type a file's bytes hold where its extension says another",
    "name_date": "the date a name states, as YYYY-MM-DD",
    # The chain of stages an experiment's files reached, in order, joined by ``>``
    # (``raw>processed>analysis``), one value per experiment folder.
    "workflow": "the stages an experiment's own files reached, earliest first, joined by >",
}

#: The families a pair answers, and whether a pair has a direction.
PAIR_FAMILIES: dict[str, bool] = {
    "replicate": False,
    "copy": False,
    "group": False,
    "reference": True,
    "derivation": True,
    # Two experiment folders that are the same experiment done on different days.
    "repeats": False,
}

FAMILIES: tuple[str, ...] = (*VALUE_FAMILIES, *PAIR_FAMILIES)

Pair = tuple[str, str]


def _canonical(pair: Iterable[str], *, directed: bool) -> Pair:
    first, second = tuple(pair)
    if first == second:
        message = f"a pair joins two paths, not {first!r} with itself"
        raise ValueError(message)
    if directed or first < second:
        return (first, second)
    return (second, first)


def pairs_of_groups(groups: Iterable[Iterable[str]]) -> tuple[Pair, ...]:
    """Every unordered pair within each group, in canonical order."""
    found: set[Pair] = set()
    for group in groups:
        members = sorted(set(group))
        for index, first in enumerate(members):
            for second in members[index + 1 :]:
                found.add((first, second))
    return tuple(sorted(found))


@dataclass(frozen=True)
class ValueFamily:
    """One value per path, ``None`` where the answer is that there is none."""

    kind: ClassVar[str] = "value"

    expected: Mapping[str, str | None]
    undecidable: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()
    complete: bool = True

    def expected_count(self) -> int:
        """How many paths hold a value the engine is asked to find."""
        return sum(1 for value in self.expected.values() if value is not None)

    def paths(self) -> Iterator[str]:
        yield from self.expected
        yield from self.undecidable
        yield from self.ignored

    def to_json(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "expected": dict(sorted(self.expected.items())),
            "undecidable": sorted(self.undecidable),
            "ignored": sorted(self.ignored),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> ValueFamily:
        return cls(
            expected=dict(sorted(dict(raw.get("expected") or {}).items())),
            undecidable=tuple(sorted(raw.get("undecidable") or ())),
            ignored=tuple(sorted(raw.get("ignored") or ())),
            complete=bool(raw.get("complete", True)),
        )


@dataclass(frozen=True)
class PairFamily:
    """Pairs that belong together, and pairs the files cannot settle."""

    kind: ClassVar[str] = "pairs"

    directed: bool
    expected: tuple[Pair, ...]
    undecidable: tuple[Pair, ...] = ()
    ignored: tuple[str, ...] = ()
    complete: bool = True
    #: The paths a partial family answers about, beyond those its pairs name.
    scope: tuple[str, ...] = field(default=())
    #: Groups the key names by their shared words (the group family's lattice): each name is the
    #: term set, and its members are the folders a person would put under it.
    named: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def expected_count(self) -> int:
        return len(self.expected)

    def paths(self) -> Iterator[str]:
        for pair in (*self.expected, *self.undecidable):
            yield from pair
        yield from self.ignored
        yield from self.scope

    def to_json(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "directed": self.directed,
            "pairs": [list(pair) for pair in self.expected],
            "undecidable": [list(pair) for pair in self.undecidable],
            "ignored": sorted(self.ignored),
            "scope": sorted(self.scope),
            **(
                {"named": {name: list(self.named[name]) for name in sorted(self.named)}}
                if self.named
                else {}
            ),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any], *, directed: bool) -> PairFamily:
        pairs = {_canonical(pair, directed=directed) for pair in raw.get("pairs") or ()}
        named = {
            str(name): tuple(sorted(members))
            for name, members in dict(raw.get("named") or {}).items()
        }
        if not directed:
            pairs.update(pairs_of_groups(raw.get("groups") or ()))
            pairs.update(pairs_of_groups(named.values()))
        elif named:
            message = "a directed family names no groups"
            raise ValueError(message)
        elif raw.get("groups"):
            message = "a directed family is written as pairs, the source first"
            raise ValueError(message)
        return cls(
            directed=directed,
            expected=tuple(sorted(pairs)),
            undecidable=tuple(
                sorted(
                    {_canonical(pair, directed=directed) for pair in raw.get("undecidable") or ()}
                )
            ),
            ignored=tuple(sorted(raw.get("ignored") or ())),
            complete=bool(raw.get("complete", True)),
            scope=tuple(sorted(raw.get("scope") or ())),
            named=dict(sorted(named.items())),
        )


Family = ValueFamily | PairFamily


@dataclass(frozen=True)
class WordMeaning:
    """What one spelling in the names means, as a person who knew the laboratory would say it."""

    #: The word or the name of two words the spelling stands for, as the word list writes it.
    means: str
    #: The Dictionary term of the word it stands for, or the name a new sense would be given
    #: where the shipped Dictionary does not read the word.
    term: str
    #: The axis of that sense (``reagent``, ``subject``, ``technique``...).
    theme_kind: str
    #: Whether the shipped Dictionary reads the word the spelling stands for.
    known: bool
    #: How the spelling was made (``original``, ``slip``, ``initials``, ``glued``, ``code``,
    #: ``condition``, ``case``, ``separator``), in canonical order.
    shapes: tuple[str, ...] = ()
    #: For a name of two words: each word's own term and axis, first word first. Empty for a
    #: single word. A person can say the name is one thing of its own (``term``), or give one of
    #: its words.
    parts: tuple[tuple[str, str], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "means": self.means,
            "term": self.term,
            "theme_kind": self.theme_kind,
            "known": self.known,
            "shapes": sorted(self.shapes),
            **({"parts": [list(part) for part in self.parts]} if self.parts else {}),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> WordMeaning:
        return cls(
            means=str(raw["means"]),
            term=str(raw["term"]),
            theme_kind=str(raw["theme_kind"]),
            known=bool(raw.get("known", False)),
            shapes=tuple(sorted(str(shape) for shape in raw.get("shapes") or ())),
            parts=tuple((str(term), str(kind)) for term, kind in raw.get("parts") or ()),
        )


@dataclass(frozen=True)
class WordsFamily:
    """What each spelling means, and which spellings are one thing."""

    #: Each spelling as the names write it (case folded) and what it means.
    spellings: Mapping[str, WordMeaning]
    #: Groups of spellings that are one thing, each group in canonical order.
    same: tuple[tuple[str, ...], ...] = ()
    #: How the spellings were made: the share rewritten, the seed, the counts by shape.
    describes: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "about": (
                "what each spelling in the names means (the word it stands for and its "
                "Dictionary term) and which spellings are one thing; not scored per path"
            ),
            "describes": dict(self.describes),
            "spellings": {
                spelling: self.spellings[spelling].to_json() for spelling in sorted(self.spellings)
            },
            "same": [list(group) for group in sorted(self.same)],
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> WordsFamily:
        return cls(
            spellings={
                str(spelling): WordMeaning.from_json(body)
                for spelling, body in sorted(dict(raw.get("spellings") or {}).items())
            },
            same=tuple(sorted(tuple(sorted(group)) for group in raw.get("same") or ())),
            describes=dict(raw.get("describes") or {}),
        )


@dataclass(frozen=True)
class Question:
    """One question a scientist writes, and the experiments that answer it."""

    text: str
    #: ``treatment`` (a treatment on a target in a cell line), ``compare`` (two levels compared),
    #: ``class`` (a class word in place of a member), ``misspelt`` (one slip).
    form: str
    #: The experiment folders carrying the conditions asked about, relative to the root.
    expected: tuple[str, ...]
    #: The words the question is about, for the report.
    about: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "form": self.form,
            "expected": list(self.expected),
            "about": list(self.about),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Question:
        return cls(
            text=str(raw["text"]),
            form=str(raw.get("form") or ""),
            expected=tuple(sorted(str(path) for path in raw.get("expected") or ())),
            about=tuple(str(word) for word in raw.get("about") or ()),
        )


@dataclass(frozen=True)
class QuestionsFamily:
    """Questions in a scientist's words, each with the experiments that answer it; scored per
    question by the experiments Ask returns at each tier, never per path."""

    questions: tuple[Question, ...]
    describes: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "about": (
                "questions a scientist writes and the experiments carrying the conditions asked "
                "about; scored by the experiments Ask returns at each tier"
            ),
            "describes": dict(self.describes),
            "questions": [question.to_json() for question in self.questions],
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> QuestionsFamily:
        return cls(
            questions=tuple(Question.from_json(body) for body in raw.get("questions") or ()),
            describes=dict(raw.get("describes") or {}),
        )


@dataclass(frozen=True)
class AnswerKey:
    """What a person would know about a folder, family by family."""

    families: Mapping[str, Family]
    #: Who or what wrote the key, and how: a generator's seed and size, or a person's minutes.
    describes: Mapping[str, Any] = field(default_factory=dict)
    #: What the spellings in the names mean. None for a key that says nothing of it.
    words: WordsFamily | None = None
    #: Questions and the experiments answering them. None where the key has none.
    questions: QuestionsFamily | None = None

    def __post_init__(self) -> None:
        unknown = sorted(set(self.families) - set(FAMILIES))
        if unknown:
            message = f"no family {', '.join(unknown)}; the families are {', '.join(FAMILIES)}"
            raise ValueError(message)
        for name, family in self.families.items():
            wanted = ValueFamily if name in VALUE_FAMILIES else PairFamily
            if not isinstance(family, wanted):
                message = f"the {name} family is answered by {wanted.kind}"
                raise TypeError(message)

    def named_paths(self) -> tuple[str, ...]:
        """Every path any family names, once, in canonical order."""
        return tuple(sorted({path for family in self.families.values() for path in family.paths()}))

    def pair_families(self) -> tuple[PairFamily, ...]:
        return tuple(family for family in self.families.values() if isinstance(family, PairFamily))

    def to_json(self) -> dict[str, Any]:
        written: dict[str, Any] = {
            "format": KEY_FORMAT,
            "format_version": KEY_FORMAT_VERSION,
            "describes": dict(self.describes),
            "families": {
                name: self.families[name].to_json() for name in FAMILIES if name in self.families
            },
        }
        if self.words is not None:
            written["words"] = self.words.to_json()
        if self.questions is not None:
            written["questions"] = self.questions.to_json()
        return written

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> AnswerKey:
        if raw.get("format") != KEY_FORMAT:
            message = f"not an answer key: format is {raw.get('format')!r}, not {KEY_FORMAT!r}"
            raise ValueError(message)
        if raw.get("format_version") != KEY_FORMAT_VERSION:
            message = (
                f"an answer key of format version {raw.get('format_version')!r}; this engine "
                f"reads version {KEY_FORMAT_VERSION}"
            )
            raise ValueError(message)
        families: dict[str, Family] = {}
        for name, body in dict(raw.get("families") or {}).items():
            if name in VALUE_FAMILIES:
                families[name] = ValueFamily.from_json(body)
            elif name in PAIR_FAMILIES:
                families[name] = PairFamily.from_json(body, directed=PAIR_FAMILIES[name])
            else:
                message = f"no family {name!r}; the families are {', '.join(FAMILIES)}"
                raise ValueError(message)
        words = raw.get("words")
        questions = raw.get("questions")
        return cls(
            families=families,
            describes=dict(raw.get("describes") or {}),
            words=None if words is None else WordsFamily.from_json(words),
            questions=None if questions is None else QuestionsFamily.from_json(questions),
        )

    def dumps(self) -> str:
        """The key as JSON text, the same bytes for the same key."""
        return json.dumps(self.to_json(), indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def load_key(path: Path) -> AnswerKey:
    """Read an answer key from its JSON file."""
    return AnswerKey.from_json(json.loads(path.read_text(encoding="utf-8")))
