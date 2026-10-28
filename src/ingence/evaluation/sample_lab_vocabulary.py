"""The vocabulary tier of the sample collection: the words, written the way laboratories write them.

``generate_sample_lab(..., vocabulary=0.3)`` writes each subject-matter word of an experiment
folder, with a chance of 0.3, in one of the shapes people actually type. Experiments named from
outside themselves are never rewritten, so the share is a chance for the words of the experiments
that can be, not a share of the whole collection. It is the collection needed to measure whether
teaching the engine its words sharpens the groupings. A first round of word answers (what a word
means, which spellings are one thing) can only sharpen the groupings if the names hold spellings
worth teaching, and the default collection, built from shipped and common biology words written
alike, holds almost none.

The shapes.

- *slip*: a letter dropped, doubled or two neighbors swapped (``tubulin`` as ``tublin``);
- *initials*: a name of two words written as its capitals (a cell line and what it carries);
- *glued*: the two words run together, with or without a capital;
- *code*: the first letters of a word, a hyphen and a number (a construct or a sample);
- *condition*: a second spelling of one condition (the first letters of the word);
- *case* and *separator*: the same word in capitals, or a name of two words joined by another
  separator. The engine reads both as the word it already knows; they are in the key so a
  measurement can show that teaching leaves them alone.

Nothing is invented. Every spelling is built from a word of ``sample_lab_words.yaml`` (the
shipped Dictionary and common biology): none is a laboratory's. A spelling that is any other word
of the collection, or that the shipped Dictionary reads, is not made. The generator never reads
the engine: the Dictionary's term for each word, and every spelling it reads that the tier could
make (``avoid``), are listed in ``sample_lab_vocabulary.yaml``, and a test holds both lists to the
Dictionary, exhaustively.

One decision for each experiment and word. A folder that is an experiment is the unit: its own
name and every name beneath it take the same spelling of a word, as one person types one spelling
through one experiment. Other experiments, copies in another place and every file outside an
experiment keep the word as it was. The text of the notes and scripts inside an experiment is
rewritten with the names, so that a script still names the files beside it; an experiment whose
files are named from outside it (a reference or a byte-for-byte copy crossing its boundary) is left
alone, so no key answer breaks. The same tier, seed and share write the same files; with no share
nothing is rewritten and nothing in this module runs.

The key's words family (:class:`~ingence.evaluation.key.WordsFamily`) holds, for every
spelling, the word it stands for and its Dictionary term, and the groups of spellings that are one
thing. A word the Dictionary does not read is given the term a person would give it (the word),
on the axis ``sample_lab_vocabulary.yaml`` names.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ingence.evaluation.key import WordMeaning, WordsFamily
from ingence.evaluation.sample_lab_words import words

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.evaluation.sample_lab_drive import Drive

__all__ = [
    "SHAPES",
    "VOCABULARY_FILE",
    "VOCABULARY_VERSION",
    "Applied",
    "Rewrite",
    "apply_vocabulary",
    "originals_only",
    "possible_spellings",
    "slip_of",
]

VOCABULARY_FILE = Path(__file__).with_name("sample_lab_vocabulary.yaml")
#: Bumped whenever the same tier, seed and share would rewrite different names.
VOCABULARY_VERSION = 1
#: Every shape, in the order the key lists them.
SHAPES = ("case", "code", "condition", "glued", "initials", "separator", "slip")
ORIGINAL = "original"
_JUDGEMENT = (
    "the term and the axis of a word the shipped Dictionary does not read are one person's "
    "judgement, not a measurement"
)


@dataclass(frozen=True)
class Rewrite:
    """One decision: this experiment writes this word (or name of two words) in this shape."""

    unit: str
    item: str
    shape: str
    #: The spelling made, case folded, as the key lists it (the code with its number).
    spelling: str


@dataclass(frozen=True)
class Applied:
    """What the tier did to a drive: the key's words family and a summary for the manifest."""

    family: WordsFamily
    rewrites: tuple[Rewrite, ...]
    summary: dict[str, Any]
    #: Each path that was given a new name, by its old one.
    moved: dict[str, str]


@dataclass(frozen=True)
class _Item:
    """A word, or a name of two words, that a rewrite may touch."""

    key: str  # case folded; words of a name joined by one space
    written: str  # as the word list writes it
    parts: tuple[str, ...]
    section: str


def _read() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(VOCABULARY_FILE.read_text(encoding="utf-8"))
    return loaded


def slip_of(word: str, operation: str, position: int) -> str:
    """``word`` with a letter dropped, doubled or swapped with the next, at ``position``."""
    if operation == "drop":
        return word[:position] + word[position + 1 :]
    if operation == "double":
        return word[:position] + word[position] + word[position:]
    return word[:position] + word[position + 1] + word[position] + word[position + 2 :]


def _slip_positions(word: str, operation: str) -> range:
    """Where an operation may act: never the first or the last letter."""
    if operation == "drop":
        return range(1, len(word) - 1)
    if operation == "double":
        return range(1, len(word))
    return range(1, len(word) - 2)


def _styled(match: str, variant: str) -> str:
    """``variant`` (lower case) in the style of the text it replaces."""
    if match.isupper():
        return variant.upper()
    if match.islower():
        return variant
    if match[:1].isupper() and match[1:].islower():
        return variant.capitalize()
    return variant


def _restyle(match: str, style: str) -> str:
    return {"upper": match.upper(), "lower": match.lower(), "title": match.capitalize()}[style]


class _Context:
    """The lists and lookups one application needs, built once."""

    def __init__(self) -> None:
        self.vocab = _read()
        self.avoid = frozenset(str(spelling) for spelling in self.vocab["avoid"])
        self.terms = {
            str(word).casefold(): (str(term), str(kind))
            for word, (term, kind) in self.vocab["terms"].items()
        }
        self.separators = "".join(self.vocab["separators"])
        self.lexicon = words()
        self.everything = self.lexicon.everything()
        self.items: dict[str, _Item] = {}
        self.sections: dict[str, str] = {}
        for kind, names in self.vocab["rewritable"].items():
            groups = getattr(self.lexicon, kind)
            for section in names:
                for written in groups[section]:
                    key = written.casefold()
                    if key not in self.items:
                        self.items[key] = _Item(key, written, (written,), section)
                        self.sections[key] = section

    def taken(self, spelling: str) -> bool:
        """Whether ``spelling`` is a word of the collection or one the Dictionary reads (the list
        ``avoid`` names every spelling the tier could make that the Dictionary reads)."""
        return spelling in self.everything or spelling in self.avoid

    def parts_of(self, item: _Item) -> tuple[tuple[str, str], ...]:
        """For a name of two words, each word's term and axis; none for a single word."""
        if len(item.parts) == 1:
            return ()
        return tuple(self.meaning_of(self.items[part.casefold()])[:2] for part in item.parts)

    def meaning_of(self, item: _Item) -> tuple[str, str, bool]:
        """The term, the axis and whether the Dictionary reads the word (or name) ``item``."""
        if len(item.parts) == 1:
            held = self.terms.get(item.key)
            if held is not None:
                return held[0], held[1], True
            return item.key, str(self.vocab["kinds"][item.section]), False
        return "_".join(item.key.split()), str(self.vocab["phrase_kind"]), False


def possible_spellings() -> dict[str, set[str]]:
    """Every new spelling the tier could make, by shape: the slips of each word, the first
    letters a code or a second spelling of a condition starts with, and the initials and the words
    run together of each pair of a cell line and a protein or tag. A test holds the list ``avoid``
    in ``sample_lab_vocabulary.yaml`` to this set, so no spelling the tier makes is a word the
    shipped Dictionary reads."""
    context = _Context()
    vocab = context.vocab
    letters, clip = int(vocab["code"]["letters"]), int(vocab["condition"]["letters"])
    found: dict[str, set[str]] = {shape: set() for shape in SHAPES}
    for item in context.items.values():
        word = item.key
        if not word.isalpha():
            continue
        if len(word) >= int(vocab["slip"]["minimum_letters"]):
            for operation in vocab["slip"]["operations"]:
                for place in _slip_positions(word, operation):
                    found["slip"].add(slip_of(word, operation, place))
        if len(word) > letters:
            found["code"].add(word[:letters])
        if len(word) >= int(vocab["condition"]["minimum_letters"]):
            found["condition"].add(word[:clip])
    cells = context.lexicon.biology["cell_lines"]
    targets = [*context.lexicon.biology["proteins_and_genes"], *context.lexicon.dictionary["tags"]]
    for cell in cells:
        for target in targets:
            found["initials"].add((cell[0] + target[0]).casefold())
            found["glued"].add((cell + target).casefold())
    return {shape: spellings - context.everything for shape, spellings in found.items()}


def _unit_of(path: str, units: set[str]) -> str | None:
    parent = path
    while parent:
        if parent in units:
            return parent
        parent = parent.rpartition("/")[0]
    return None


def _all_paths(d: Drive) -> list[str]:
    return sorted({*d.files, *d.folders, *d.empty_folders, *d.links})


def _phrases(d: Drive, context: _Context) -> dict[str, _Item]:
    """The names of two words the collection's lines of work are called (a cell line and a
    protein or tag), which the key's groups name."""
    found: dict[str, _Item] = {}
    for name in sorted(d.named_groups):
        parts = tuple(name.split())
        if len(parts) == 2 and all(part.casefold() in context.items for part in parts):
            written = " ".join(context.items[part.casefold()].written for part in parts)
            found[name.casefold()] = _Item(name.casefold(), written, parts, "phrase")
    return found


def _pattern(items: list[_Item], separators: str) -> re.Pattern[str]:
    """One expression matching any of the items as whole words, a name of two words first."""
    ordered = sorted(items, key=lambda item: (-len(item.key), item.key))
    gap = f"[{re.escape(separators)}]"
    alternatives = [gap.join(re.escape(part) for part in item.parts) for item in ordered]
    return re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(alternatives) + r")(?![A-Za-z0-9])", re.IGNORECASE
    )


def _key_of(match: str, separators: str) -> str:
    return re.sub(f"[{re.escape(separators)}]", " ", match.casefold())


def apply_vocabulary(
    d: Drive,
    *,
    fraction: float,
    seed: int,
    tier: str,
) -> Applied:
    """Rewrite the subject-matter words of ``d`` in place, and return the words family that says
    what every spelling means.

    ``fraction`` is the chance that an experiment (one that can be rewritten) writes a given word
    in a shape other than the word list's own; ``seed`` and ``tier`` name the draw, so the same
    arguments rewrite the same names. Nothing is read from a filesystem, and nothing here changes
    a file outside an experiment.
    """
    if not 0.0 < fraction <= 1.0:
        message = f"the share of words to rewrite is more than 0 and at most 1, not {fraction}"
        raise ValueError(message)
    context = _Context()
    vocab = context.vocab
    rng = random.Random(f"ingence-sample-lab-vocabulary:{VOCABULARY_VERSION}:{tier}:{seed}")  # noqa: S311
    separators = context.separators
    phrases = _phrases(d, context)
    every_item = [*phrases.values(), *context.items.values()]
    pattern = _pattern(every_item, separators)
    by_key = {item.key: item for item in every_item}

    paths = _all_paths(d)
    experiments = {
        path
        for path, value in d.values.get("experiment_folder", {}).items()
        if value == "experiment"
    }
    units = sorted(
        path for path in experiments if not _unit_of(path.rpartition("/")[0], experiments)
    )
    unit_set = set(units)
    members: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        owner = _unit_of(path, unit_set)
        if owner is not None:
            members[owner].append(path)

    # -- an experiment whose files are named from outside it is left alone -------------------
    crossed: set[str] = set()
    text_kinds = set(vocab["text_kinds"])
    for pair_table in (d.pairs, d.undecidable_pairs):
        for source, target in pair_table.get("reference", set()):
            one, other = _unit_of(source, unit_set), _unit_of(target, unit_set)
            if one != other:
                crossed.update(unit for unit in (one, other) if unit is not None)
    for entry in d.unscored.get("near_copies", []):
        crossed.update(
            owner
            for owner in (
                _unit_of(str(path), unit_set)
                for path in (entry if isinstance(entry, (list, tuple)) else ())
            )
            if owner is not None
        )
    by_bytes: dict[bytes, list[str]] = defaultdict(list)
    for path, data in d.files.items():
        if data and d.kinds[path] in text_kinds:
            by_bytes[data].append(path)
    #: A text file saved again outside any experiment (a backup, a copy in a misc folder) is
    #: rewritten with the original, so the two stay byte for byte alike. Two experiments sharing a
    #: text file's bytes cannot both keep their own spelling, so both are left alone.
    twins: dict[str, list[str]] = defaultdict(list)
    for same in by_bytes.values():
        owners = {_unit_of(path, unit_set) for path in same}
        named = {owner for owner in owners if owner is not None}
        if len(named) > 1:
            crossed.update(named)
        elif named:
            twins[next(iter(named))].extend(
                path for path in same if _unit_of(path, unit_set) is None
            )

    # -- the decisions, experiment by experiment, word by word -------------------------------
    meanings: dict[str, WordMeaning] = {}
    chosen: dict[str, dict[str, Rewrite]] = {}
    details: dict[str, dict[str, Any]] = {}
    letters_of_code: dict[str, set[str]] = defaultdict(set)
    for item in context.items.values():
        letters = item.key[: int(vocab["code"]["letters"])]
        letters_of_code[letters].add(item.key)

    def remember(spelling: str, item: _Item, shape: str) -> bool:
        """Record what a spelling means; False if it already means something else."""
        held = meanings.get(spelling)
        term, kind, known = context.meaning_of(item)
        if held is not None and held.means != item.written:
            return False
        shapes = sorted({*(held.shapes if held else ()), shape})
        meanings[spelling] = WordMeaning(
            item.written, term, kind, known, tuple(shapes), context.parts_of(item)
        )
        return True

    for unit in units:
        if unit in crossed:
            continue
        present: set[str] = set()
        for path in members[unit]:
            start = len(unit.rpartition("/")[0]) + 1 if "/" in unit else 0
            for component in path[start:].split("/"):
                present.update(
                    _key_of(found.group(1), separators) for found in pattern.finditer(component)
                )
        decisions: dict[str, Rewrite] = {}
        extra: dict[str, Any] = {}
        for key in sorted(present):
            item = by_key[key]
            if rng.random() >= fraction:
                continue
            kind = "phrase" if len(item.parts) == 2 else "word"
            weights = {
                shape: weight
                for shape, weight in vocab["weights"][kind].items()
                if weight and (shape != "condition" or item.section in vocab["condition_sections"])
            }
            if not weights:
                continue
            shape = rng.choices(sorted(weights), [weights[name] for name in sorted(weights)])[0]
            made = _spelling(shape, item, context, rng, letters_of_code, separators)
            if made is None:
                continue
            spelling, detail = made
            # A spelling must mean one thing: a second meaning for it is no rewrite.
            if not remember(spelling, item, shape):
                continue
            decisions[key] = Rewrite(unit, item.written, shape, spelling)
            extra[key] = detail
        if decisions:
            chosen[unit] = decisions
            details[unit] = extra

    # -- the new names ------------------------------------------------------------------------
    moved: dict[str, str] = {}
    rewritten: dict[str, bytes] = {}
    applied: list[Rewrite] = []
    skipped = 0
    taken = {path.casefold() for path in paths}
    for unit, decisions in sorted(chosen.items()):
        replacer = _Replacer(decisions, details[unit], by_key, separators)
        base = unit.rpartition("/")[0]
        start = len(base) + 1 if base else 0
        renamed: dict[str, str] = {}
        for path in members[unit]:
            tail = path[start:].split("/")
            joined = "/".join(pattern.sub(replacer, part) for part in tail)
            renamed[path] = f"{base}/{joined}" if base else joined
        # A decision that changed no name (a case the names already have, a separator already
        # there) is no rewrite: it is not counted and it does not touch the text either.
        replacer.keep_what_changed_a_name()
        changed = {old: new for old, new in renamed.items() if old != new}
        if not changed:
            skipped += 1
            continue
        news = [new.casefold() for new in renamed.values()]
        outside = taken - {old.casefold() for old in renamed}
        if len(set(news)) != len(news) or outside.intersection(news):
            skipped += 1
            continue
        moved.update(changed)
        texts = [*renamed.items(), *((path, path) for path in twins.get(unit, []))]
        for path, new in texts:
            if path in d.files and d.kinds[path] in text_kinds and d.files[path]:
                try:
                    text = d.files[path].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                again = pattern.sub(replacer, text)
                if again != text:
                    rewritten[new] = again.encode("utf-8")
        applied.extend(replacer.active.values())
        taken = (taken - {old.casefold() for old in changed}) | {
            new.casefold() for new in changed.values()
        }
    if moved:
        d.rename(moved, rewritten)

    by_shape = {
        shape: sum(1 for rewrite in applied if rewrite.shape == shape)
        for shape in SHAPES
        if any(rewrite.shape == shape for rewrite in applied)
    }
    words_family = _family(
        d,
        context,
        meanings,
        applied,
        {
            "tier": "vocabulary",
            "version": VOCABULARY_VERSION,
            "fraction": fraction,
            "seed": seed,
            "rewrites": len(applied),
            "by_shape": by_shape,
            "judgement": _JUDGEMENT,
        },
    )
    summary = {
        "version": VOCABULARY_VERSION,
        "fraction": fraction,
        "seed": seed,
        "experiments": len(units),
        "experiments_rewritten": len({rewrite.unit for rewrite in applied}),
        "experiments_left_alone": len(crossed),
        "experiments_not_rewritten": skipped,
        "rewrites": len(applied),
        "by_shape": by_shape,
        "names_renamed": len(moved),
        "files_with_text_rewritten": len(rewritten),
    }
    return Applied(words_family, tuple(applied), summary, dict(moved))


def originals_only(d: Drive) -> Applied:
    """The words family of the original words the names hold, with no word rewritten.

    The tier-off arm of a measurement (the same pool as the default sample, nothing rewritten): the
    key says what each subject-matter word in the names means, so an oracle can teach the original
    words alone, and the groupings' room to improve can be read beside the tier-on arm. Nothing in
    ``d`` changes.
    """
    context = _Context()
    family = _family(
        d,
        context,
        {},
        [],
        {
            "tier": "off",
            "version": VOCABULARY_VERSION,
            "originals_only": True,
            "rewrites": 0,
            "judgement": _JUDGEMENT,
        },
    )
    summary = {
        "version": VOCABULARY_VERSION,
        "applied": False,
        "originals_only": True,
        "rewrites": 0,
    }
    return Applied(family, (), summary, {})


class _Replacer:
    """The replacement for each occurrence of an item that one experiment has decided to rewrite.

    Called for every match of the vocabulary's pattern: an item with no decision comes back as it
    was. It remembers which decisions changed a text, so the caller can drop the rest.
    """

    def __init__(
        self,
        decisions: Mapping[str, Rewrite],
        details: Mapping[str, Any],
        by_key: Mapping[str, _Item],
        separators: str,
    ) -> None:
        self.active = dict(decisions)
        self._details = details
        self._by_key = by_key
        self._separators = separators
        self._changed: set[str] = set()

    def __call__(self, found: re.Match[str]) -> str:
        text = found.group(0)
        key = _key_of(text, self._separators)
        decision = self.active.get(key)
        if decision is None:
            return text
        made = _text_for(decision, self._details[key], text, self._by_key[key], self._separators)
        if made != text:
            self._changed.add(key)
        return made

    def keep_what_changed_a_name(self) -> None:
        """Keep the decisions that changed a text so far, and forget the others."""
        self.active = {key: made for key, made in self.active.items() if key in self._changed}


def _spelling(
    shape: str,
    item: _Item,
    context: _Context,
    rng: random.Random,
    letters_of_code: Mapping[str, set[str]],
    separators: str,
) -> tuple[str, Any] | None:
    """The spelling a shape makes of an item and what the rewrite needs to repeat it, or None."""
    vocab = context.vocab
    word = item.key
    if shape == "slip":
        if (
            len(item.parts) != 1
            or not word.isalpha()
            or len(word) < int(vocab["slip"]["minimum_letters"])
        ):
            return None
        for _attempt in range(12):
            operation = rng.choice(vocab["slip"]["operations"])
            places = _slip_positions(word, operation)
            if not places:
                continue
            made = slip_of(word, operation, rng.choice(places))
            if made != word and not context.taken(made):
                return made, made
        return None
    if shape == "case":
        style = rng.choice(["upper", "lower", "title"])
        return word, style
    if shape == "code":
        letters = int(vocab["code"]["letters"])
        if not word.isalpha() or len(word) <= letters:
            return None
        head = word[:letters]
        if letters_of_code[head] != {word} or context.taken(head):
            return None
        number = rng.randint(int(vocab["code"]["first_number"]), int(vocab["code"]["last_number"]))
        return f"{head}-{number}", (head, number)
    if shape == "condition":
        clip = int(vocab["condition"]["letters"])
        if not word.isalpha() or len(word) < int(vocab["condition"]["minimum_letters"]):
            return None
        head = word[:clip]
        if context.taken(head) or any(
            other.startswith(head) for other in context.items if other != word
        ):
            return None
        return head, head
    if shape == "initials":
        capitals = "".join(part[:1].upper() for part in item.parts)
        if context.taken(capitals.casefold()):
            return None
        return capitals.casefold(), capitals
    if shape == "glued":
        lowered = rng.random() < 0.5
        glued = "".join(item.parts)
        if lowered and context.taken(glued.casefold()):
            return None
        return glued.casefold(), lowered
    if shape == "separator":
        other = rng.choice(list(separators))
        return f"{item.parts[0].casefold()}{other}{item.parts[1].casefold()}", other
    return None


def _text_for(decision: Rewrite, detail: Any, match: str, item: _Item, separators: str) -> str:
    """The text that replaces one matched occurrence of the item in a name."""
    shape = decision.shape
    if shape == "slip" or shape == "condition":
        return _styled(match, str(detail))
    if shape == "case":
        return _restyle(match, str(detail))
    if shape == "code":
        head, number = detail
        return f"{_styled(match, head)}-{number}"
    if shape == "initials":
        return str(detail)
    if shape == "glued":
        pieces = re.split(f"[{re.escape(separators)}]", match)
        glued = "".join(pieces)
        return glued.lower() if detail else glued
    other = str(detail)
    return re.sub(f"[{re.escape(separators)}]", lambda _found: other, match, count=1)


def _family(
    d: Drive,
    context: _Context,
    meanings: Mapping[str, WordMeaning],
    applied: list[Rewrite],
    describes: dict[str, Any],
) -> WordsFamily:
    """The words family: every spelling a rewrite made, the original of each word the names hold,
    and the spellings that are one thing. A spelling carries only the shapes of the rewrites that
    were made."""
    shapes_of: dict[str, set[str]] = defaultdict(set)
    for rewrite in applied:
        shapes_of[rewrite.spelling].add(rewrite.shape)
    kept: dict[str, WordMeaning] = {
        spelling: replace(meanings[spelling], shapes=tuple(sorted(shapes)))
        for spelling, shapes in shapes_of.items()
    }
    # The original of every subject-matter word the names hold: what a person would say of a word
    # nobody rewrote is also in the key.
    seen: set[str] = set()
    pattern = _pattern(list(context.items.values()), context.separators)
    for path in _all_paths(d):
        for component in path.split("/"):
            seen.update(found.group(1).casefold() for found in pattern.finditer(component))
    for key in sorted(seen):
        item = context.items[key]
        term, kind, known = context.meaning_of(item)
        held = kept.get(key)
        shapes = sorted({ORIGINAL, *(held.shapes if held else ())})
        kept[key] = WordMeaning(item.written, term, kind, known, tuple(shapes), ())
    groups: dict[str, set[str]] = defaultdict(set)
    for spelling, meaning in kept.items():
        groups[meaning.means].add(spelling)
    same = tuple(sorted(tuple(sorted(group)) for group in groups.values() if len(group) > 1))
    return WordsFamily(spellings=kept, same=same, describes=describes)
