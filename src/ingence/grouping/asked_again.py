"""Find the answer to a group question that was answered before the lattice's words changed.

A group question names its words: a set (``a group: kelt vorn zib``), a deviation
(``brisa, added to the group kelt vorn``) or a proposed group
(``kelt vorn [20240301 kelt vorn brisa and 1 more]``). Which words it names depends on
``groups.words_as``, so a person's answer, kept under the question as it was worded
(``question:<rule>:<value>``, or named in the answer a page wrote to ``group:<terms>``), would stop
matching the question the next run raises the moment the setting, or the Dictionary, changed a
spelling into its term. The question would be asked again, and a proposal the person confirmed would
stop grouping. :class:`AskedAgain` finds the answer again. It finds the answer only when it is
certain that the question is the same one. Anything it cannot find exactly is asked again, which is
the safe direction.

What counts as the same question:

- A question worded exactly as it was answered is the same question (the Record's own target),
  except a proposal. A proposal's text names at most three words and the first folder and a
  count, so it must also have the members it was answered with, wherever the run that the answer
  was made against can still be read. Where that run is gone the text is all there is, and the
  approval stands.
- A set or a deviation is the same where its words, read through the word map
  (:meth:`~ingence.grouping.lattice_words.WordMap.answer`), equal the words now: the whole set for
  a set, the word and the core for a deviation.
- A proposed group is the same only where it has exactly the same members, the experiments it
  names. Its wording ends in the first member and a count (``[first and N more]``), which says too
  little: a different group behind the same first folder name and count is a different group. The
  members of the question as it was answered are those of the run that the answer was made
  against, which raised the question then. The members now are the proposal's. The finder looks
  for a proposal in other words only where its stored words read differently through the map. A
  question worded as it was is found only as it was written.

Two stored answers may read as one question. An approval of ``brisa kelt`` and a decline of
``brisal kelt`` are both ``brisa_stain kelt`` once the two spellings are one term. The latest by
sequence is then the answer in force, as it is for any target in the Record.

Only the words move. The answer keeps its author, its moment and its target in the Record. This
module only reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.grouping.resolutions import deviation_parts, lattice_terms
from ingence.record.questions import TARGET_PREFIX
from ingence.record.targets import group_terms_named_by

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from ingence.grouping.lattice_words import WordMap
    from ingence.storage.catalog import Catalog

__all__ = ["AskedAgain", "ProposalMembers"]

#: A proposed group's value: up to three of its words, then the files it names.
_PROPOSAL = re.compile(r"^(?P<words>.*) \[(?P<files>.+ and \d+ more)\]$")

type _Key = tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _Asked:
    """One group question, read out of its target."""

    #: ``set`` (a whole set of words, asked as a lattice question or answered on its group),
    #: ``deviation`` or ``proposal``.
    kind: str
    #: The rule that raised it; empty for a set, which a group's answer settles whoever asked.
    rule: str
    #: The words it names: the set, the deviation's core, or the proposal's shown words.
    words: tuple[str, ...]
    #: A deviation's word, and whether it was added to the core.
    word: str = ""
    added: bool = False
    #: A proposal's files, as its value names them.
    files: str = ""

    def key(self, words: tuple[str, ...], word: str = "") -> _Key:
        """What it asks, as a key two questions worded in other words can share."""
        if self.kind == "set":
            return ("set", words)
        if self.kind == "deviation":
            return ("deviation", self.rule, self.added, word, words)
        return ("proposal", self.rule, self.files)


def _asked_by(target: str) -> _Asked | None:
    """The group question a target names, or None for any other target."""
    named = group_terms_named_by(target)
    if named is not None:
        return _Asked("set", "", named)
    prefix, _, rest = target.partition(":")
    rule, _, value = rest.partition(":")
    if prefix != TARGET_PREFIX or not rule or not value:
        return None
    lattice = lattice_terms(value)
    if lattice is not None:
        return _Asked("set", "", lattice)
    deviation = deviation_parts(value)
    if deviation is not None:
        word, added, core = deviation
        return _Asked("deviation", rule, core, word.casefold(), added)
    proposal = _PROPOSAL.match(value)
    if proposal is not None:
        shown = tuple(sorted(set(proposal.group("words").casefold().split())))
        return _Asked("proposal", rule, shown, files=proposal.group("files"))
    return None


class ProposalMembers:
    """The members of the proposals a run raised, read once for each run.

    A proposed group's findings are one for each member experiment, each carrying the group's
    question, so the members of a question are the folders its findings are about. They are read
    from the run the question was asked in (``run_id``), which is how a proposal answered in other
    words is known to be the same group now: it has the same members.
    """

    def __init__(self, project: Path) -> None:
        self._project = project
        self._held: dict[tuple[str, str], dict[str, frozenset[str]]] = {}

    @staticmethod
    def in_catalog(catalog: Catalog, rule_id: str) -> dict[str, frozenset[str]]:
        """Each question the rule raised in this run, with the folders its findings are about."""
        try:
            rows = catalog.query(
                """
                SELECT s.value, e.relative_path
                FROM (
                    SELECT u.value AS value, regexp_replace(f.subject, '^folder:', '') AS entry_id
                    FROM findings f, unnest(f.raised) AS u(value) WHERE f.rule_id = ?
                ) s JOIN entries e ON e.entry_id = s.entry_id
                """,
                [rule_id],
            ).fetchall()
        except Exception:  # a run without the rule's findings
            return {}
        found: dict[str, set[str]] = {}
        for value, path in rows:
            found.setdefault(str(value), set()).add(str(path))
        return {value: frozenset(paths) for value, paths in found.items()}

    @classmethod
    def now(cls, catalog: Catalog) -> Callable[[str], frozenset[str] | None]:
        """The members of a proposal as this run raises it, read once for each rule."""
        held: dict[str, dict[str, frozenset[str]]] = {}

        def members(target: str) -> frozenset[str] | None:
            asked = _asked_by(target)
            if asked is None or asked.kind != "proposal":
                return None
            if asked.rule not in held:
                held[asked.rule] = cls.in_catalog(catalog, asked.rule)
            return held[asked.rule].get(target.partition(":")[2].partition(":")[2])

        return members

    def of(self, run_id: str, target: str) -> frozenset[str] | None:
        """The members of the proposal ``target`` names in the run ``run_id`` raised it in, or
        None where that run is not readable or did not raise it."""
        from ingence.storage.catalog import open_catalog

        asked = _asked_by(target)
        if asked is None or asked.kind != "proposal":
            return None
        key = (run_id, asked.rule)
        if key not in self._held:
            try:
                with open_catalog(self._project, run_id=run_id) as catalog:
                    self._held[key] = self.in_catalog(catalog, asked.rule)
            except Exception:  # a run no longer readable raised nothing to find
                self._held[key] = {}
        return self._held[key].get(target.partition(":")[2].partition(":")[2])


class AskedAgain[T]:
    """Finds what was given to a group question, wherever the question is worded in other words.

    ``stored`` is what the Record holds, by target: ``question:<rule>:<value>`` for a question
    answered on its own, ``group:<terms>`` for a group a page answered, or any target at all (one
    that names no group question is found only as it was written). ``reading`` makes the
    run's word map and is called only when a target is not found as written, so a run whose
    answers still match pays nothing.

    ``rank`` orders two stored answers that read as one question, the greater winning (the
    answer's sequence in the Record). Without it, the one stored first wins. ``members_then`` says
    the members of a proposal as the stored answer was given to it (``members_then(target,
    given)``, None where unknown), and ``members_now`` those of a proposal as it is raised now
    (``members_now(target)``). A caller that already holds them hands them to :meth:`find`. The
    finder does not find a proposal that cannot be shown to have the same members.
    """

    def __init__(
        self,
        reading: Callable[[], WordMap],
        stored: Mapping[str, T],
        *,
        rank: Callable[[T], int] | None = None,
        members_then: Callable[[str, T], frozenset[str] | None] | None = None,
        members_now: Callable[[str], frozenset[str] | None] | None = None,
    ) -> None:
        self._reading = reading
        self._stored = stored
        self._rank = rank
        self._members_then = members_then
        self._members_now = members_now
        self._index: dict[_Key, list[tuple[str, T]]] | None = None

    def __bool__(self) -> bool:
        return bool(self._stored)

    def find(self, target: str, *, members: frozenset[str] | None = None) -> T | None:
        """What was given to this question: as written first, then as the words now read it."""
        if target in self._stored:
            given = self._stored[target]
            return given if self._same_members(target, given, members) else None
        asked = _asked_by(target)
        if asked is None:
            return None
        candidates = self._indexed().get(asked.key(asked.words, asked.word), ())
        if asked.kind == "proposal":
            if not candidates or self._members_then is None:
                return None
            now = members if members is not None else self._now(target)
            if now is None:
                return None
            candidates = [
                (stored, given)
                for stored, given in candidates
                if self._members_then(stored, given) == now
            ]
        if not candidates:
            return None
        rank = self._rank
        if rank is None:
            return candidates[0][1]
        # The latest answer wins where two read as one question (see the module docstring).
        return max(candidates, key=lambda item: rank(item[1]))[1]

    def holds(self, target: str, given: T) -> bool:
        """Whether an answer found for ``target`` by its text is still the answer to it: false only
        where it is the answer stored under that question and the question is a proposal whose
        members are not those it was answered with."""
        if self._stored.get(target) is not given:
            return True
        return self._same_members(target, given, None)

    def _same_members(self, target: str, given: T, members: frozenset[str] | None) -> bool:
        """Whether a proposal worded exactly as it was answered is the same group.

        Words and a count do not make a group. The text names at most three words and the first
        folder and how many follow, so a later run can propose different members behind the very
        same text. Where the run that the answer was made against can still be read, the members it
        held must be the members now. Where that run is gone, the text is all there is, and the
        approval is not lost for it. Members that cannot be told now are not the same, so the
        question is asked again, which is the safe direction.
        """
        if self._members_then is None:
            return True
        asked = _asked_by(target)
        if asked is None or asked.kind != "proposal":
            return True
        then = self._members_then(target, given)
        if then is None:
            return True
        now = members if members is not None else self._now(target)
        return now is not None and now == then

    def _now(self, target: str) -> frozenset[str] | None:
        return self._members_now(target) if self._members_now is not None else None

    def _indexed(self) -> dict[_Key, list[tuple[str, T]]]:
        """Every stored group question, once, under the keys it has in the words held now.

        A proposal is indexed only where its words read differently through the map: a proposal
        worded as it was is found as it was written, or not at all.
        """
        if self._index is None:
            reading = self._reading()
            index: dict[_Key, list[tuple[str, T]]] = {}
            for target, given in self._stored.items():
                asked = _asked_by(target)
                if asked is None:
                    continue
                readings = reading.answer(asked.words)
                if asked.kind == "proposal" and readings == [asked.words]:
                    continue
                # A deviation's word is read as every word is: a term, or the spellings of one.
                spoken = [""]
                if asked.word:
                    spoken = [found[0] for found in reading.answer([asked.word])]
                for words in readings:
                    for word in spoken:
                        index.setdefault(asked.key(words, word), []).append((target, given))
            self._index = index
        return self._index
