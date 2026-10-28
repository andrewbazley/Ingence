"""The Dictionary: what a string denotes when it appears in this data pool.

The layer that turns an observed token into something the Rulebook can reason
over. Three rules govern it and each exists because the obvious alternative
fails.

No entry licenses a conclusion on its own. A single hit is weak evidence: enough to propose, never
enough to assert. That is what makes an exhaustive dictionary free rather than costly. A dictionary
is only a burden when a wrong entry *causes* a wrong inference somebody then has to hunt down; if an
entry is a proposal whose weight depends on corroboration, an irrelevant one never fires, costs a
few bytes, and is never seen. Exhaustiveness and low cost are therefore compatible, which is why
this ships with several thousand surface forms rather than a cautious few hundred.

Hits compound across distinct senses, never across spellings of one. Three independent facts about a
file are three pieces of evidence: a technique, a reagent and a stage. The same term written three
ways is one fact seen three times, and compounding it would make every verbose filename maximally
confident. Compounding therefore happens after surface normalisation, at the level of the *theme
kind*, and a second hit on a kind already seen adds nothing.

A few hits still beat none. Support is not all-or-nothing. One hit is worth more than zero and less
than three, and the levels say so, because relegating a file with two recognised terms to the same
standing as a file with none throws away the evidence this layer exists to gather.

Support is a level rather than a number, for the same reason tiers are used
everywhere else: a number that looks like a probability and is not is a liability
in something meant to be published. A tiebreak value exists to order within a
level and for nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ingence.core.ids import (
    PROJECT_SCOPE,
    Scope,
    ScopeKind,
    canonical_json,
    dictionary_term_id,
    digest_text,
)
from ingence.core.schema import (
    MATCH_QUALITY_ORDER,
    DictionaryEntry,
    DictionaryOrigin,
    DictionaryStatus,
    MatchQuality,
)
from ingence.dictionary.programs import LAYOUTS_KEY, ProgramLayout, layouts_from, program_layouts

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from ingence.dictionary.laboratory_packs import InstalledPack
    from ingence.dictionary.spelling import SpellingIndex

__all__ = [
    "BUILTIN_DIRECTORY",
    "EXTENDS",
    "EXTENSION_ONLY",
    "ORIGINS",
    "PACKS_DIRECTORY",
    "PLURAL_MINIMUM",
    "WRITTEN_BY",
    "Contested",
    "DictionaryStore",
    "Hit",
    "MatchQuality",
    "Resolution",
    "Section",
    "Sense",
    "Support",
    "fold_extensions",
    "load_sections",
    "load_shipped_sections",
    "origin_rank",
    "scope_applies",
    "singulars_of",
]

#: Where the shipped universal sections live: vocabulary every laboratory uses, whatever its
#: field.
BUILTIN_DIRECTORY = Path(__file__).parent / "builtin"

#: Where the shipped domain sections live, one directory each. Every one loads, level with the
#: universal sections, except for a domain that a person vetoed.
PACKS_DIRECTORY = Path(__file__).parent / "packs"

#: Precedence between origins. A person's own entry beats everything. What shipped and what a pack
#: adds stand level. The order is what lets somebody correct a shipped sense locally without
#: destroying it: the builtin entry stays, and is shadowed.
#:
#: Precedence is a winner and not a tiebreak, which is why ``PACK`` sits level with ``BUILTIN``.
#: Unequal precedence means the higher sense wins outright, the loser is recorded as shadowed and
#: nothing is reported. Equal precedence means a tie, which resolves to neither and says so. A pack
#: ranked above the core, at 30 for example, would make ``ms`` mean mass spectrometry silently and
#: shadow the millisecond. The same would happen to all 37 of the shipped vocabulary's ambiguities
#: between the core and a domain.
#:
#: A pack is not a decision, it is more vocabulary. Letting a pack win converts an install into an
#: interpretation, made on the user's behalf and never mentioned. A ``USER`` sense is a person
#: saying what a word means in their data, carries a scope, and still beats both. So "in this
#: folder ``ms`` means mass spectrometry" remains expressible and is the way a tie is meant to be
#: settled.
#:
#: The cost is real. Between this and a person answering, a collision that would resolve with a
#: higher pack precedence abstains, so the engine says less about a pool with a pack installed. No
#: result is a valid output, but it is still a loss in what the engine can say. That is why what a
#: pack disagrees with the core about must be visible before it bites rather than after:
#: :meth:`DictionaryStore.collisions`.
#:
#: ``DISCOVERED`` stands level with both, so only a person's decision wins. A discovered sense is
#: "a proposal until a person promotes it", and a proposal that outranked published vocabulary would
#: be the engine's induction deciding over what somebody wrote down: the pack problem again, from
#: the other side. Level with the others, a proposal that collides with the core or a pack is
#: reported as a tie and settled by an answer, which is ``USER``'s 40. Nothing in the engine
#: populates ``DISCOVERED``.
ORIGIN_PRECEDENCE: dict[DictionaryOrigin, int] = {
    DictionaryOrigin.USER: 40,
    DictionaryOrigin.PACK: 10,
    DictionaryOrigin.DISCOVERED: 10,
    DictionaryOrigin.BUILTIN: 10,
}


def origin_rank(sense: Sense) -> int:
    """Return where a sense stands among the origins: :data:`ORIGIN_PRECEDENCE`, nothing else.

    There is one dictionary, and every shipped section stands at the core's level. A shipped domain
    section's sense stands level with a universal one, so a spelling that two of them hold is a tie
    that the resolver reads from the folder's own files (``dictionary.domain_skew``). Otherwise the
    resolver takes the universal sense, marked assumed. A sense's standing does not depend on a
    project accepting a pack, because every section loads.
    """
    return ORIGIN_PRECEDENCE.get(sense.origin, 0)


#: Narrower scopes win over wider ones. "In this folder, that word means this"
#: is the correction people actually want to make, and without it they disable a
#: good entry globally to fix one directory.
SCOPE_PRECEDENCE: dict[ScopeKind, int] = {
    ScopeKind.SELECTION: 50,
    ScopeKind.FOLDER: 40,
    #: Below a folder and above a level, because a person who points at a folder is
    #: talking about the files in front of them and a person who names a file type is
    #: stating a general rule about a kind. Where the two collide the pointing wins.
    ScopeKind.FILE_TYPE: 35,
    ScopeKind.LEVEL: 30,
    ScopeKind.ROOT: 20,
    ScopeKind.PROJECT: 10,
}


#: The property a sense sets to say which of its spellings bind in an extension and
#: nowhere else: ``true`` for all of them, or the list of surfaces it applies to.
#:
#: The spelling of a format is its extension, and many of those spellings are also ordinary
#: words. ``.log``, ``.bin``, ``.out``, ``.raw``, ``.key``, ``.mod``, ``.h``: each is a format
#: after a dot and a word before one, and a laboratory writes both. Shipping the format sense
#: unrestricted made the word unbindable, because two senses of one kind at one precedence tie and
#: resolve to neither, so ``analysis_log.txt`` stopped saying anything about a log. The shipped
#: sections avoided that by spelling sixteen formats ``mat_file``, ``raw_binary``, ``nc_format``
#: and so on: surfaces no filename carries, which left the real extension bound to nothing at all.
#:
#: A spelling marked here is a candidate only where the caller has restricted the slot to the
#: sense's own kind, which is what the extension slot does and nothing else does
#: (``permitted_kinds``).
#:
#: The mark is per surface, because a per-sense mark was measured and cost too much. Restricting
#: whole senses took many resolved word bindings off a laboratory's census: a name holding
#: ``csv``, ``xlsx`` or ``npy`` as a word means the format it spells, and only the handful of
#: spellings that already mean something else needed restricting at all. The rule the shipped
#: sections follow is exactly that: a format's spelling is extension-only where the same spelling
#: is already another sense. So ``mat`` and ``nc``, which collide with nothing, stay readable
#: anywhere.
EXTENSION_ONLY = "extension_only"

#: The property a format sense declares its spellings' origins under: each spelling, the vendor,
#: and who writes a file of it (``WRITTEN_BY``). One image type holds TIFF and ND2 alike, because
#: both hold images. What separates them is that only one is written by an instrument as it
#: acquires, and that is a fact about a spelling, not about the type.
ORIGINS = "origins"

#: Who writes a file of a declared spelling: ``instrument`` (as it acquires) or ``software``
#: (afterwards, a converter or an analysis program).
WRITTEN_BY = "written_by"

#: The property an entry sets to say that it adds spellings to a sense loaded elsewhere rather
#: than declaring a sense of its own.
#:
#: One type has spellings that arrive with the field that writes them. An image is one type (the
#: proprietary file types only limit its origins), and the core must know it whatever domain a
#: project names, because a laboratory of any kind holds TIFF, PNG and JPEG. But ND2, CZI and LIF
#: are microscopy's, and a project that names no microscopy has no reason to carry them. So the
#: core declares ``image_format`` with the spellings anything can write, and the microscopy pack
#: declares an entry of the same term with ``extends: true``, holding the instruments' spellings
#: and their origins. Loaded together they are one sense, exactly the sense written whole. The
#: core alone is the core's half. Two senses of one term cannot replace this design: a term names
#: one type, its identifier is made from the term, and a person scoping a statement to "my image
#: files" means all of them.
#:
#: :func:`fold_extensions` says what folds and what is refused.
EXTENDS = "extends"


def _binds_outside_an_extension(sense: Sense, token: str) -> bool:
    """Whether this sense may answer for this spelling where a format is not the only kind.

    The cheap test first: almost no sense declares the property at all, and this runs on
    every candidate of every lookup.
    """
    restricted = sense.properties.get(EXTENSION_ONLY)
    if not restricted:
        return True
    if restricted is True:
        return False
    spelled = token.casefold()
    loose = _loose_key(token)
    return not any(
        spelled == str(surface).casefold() or loose == _loose_key(str(surface))
        for surface in restricted
    )


def scope_applies(sense_scope: Scope, query_scope: Scope) -> bool:
    """Whether a sense declared at one scope may bind at another.

    Separate from :data:`SCOPE_PRECEDENCE`, and the two answer different
    questions. Precedence decides which of several *applicable* senses wins.
    This decides which are applicable at all, and without it precedence ranks
    senses that had no business being candidates.

    A wider scope reaches inward and a narrower one does not reach outward. A project-scoped sense
    binds anywhere in the project. A folder-scoped sense binds in that folder and beneath it,
    because "in this folder, that word means this" is a statement about a subtree rather than about
    one directory listing. Neither binds the other way: a sense somebody declared for one folder
    must not answer a question asked about the project.

    One pair is undecidable and raises rather than guessing. A root scope names a root identifier
    and a folder scope names a path relative to a root, so nothing in either value says whether the
    folder lies inside the root. The two honest answers are to refuse, or to invent a containment
    that a :class:`Scope` cannot support. Refusing is the choice, because silently admitting or
    silently denying gives a value that depends on something the key never carried, wrong only once
    a second case exists, and wrong invisibly.

    A level names an equivalence class and a selection names an explicit list of
    entries. Neither carries an order, so both match exactly or not at all.

    A file type is not a region, and the refusal above does not extend to it. A folder path says
    nothing about a file's type, so a question asked only about a folder is not a question about a
    type. A sense scoped to a type does not answer it. That is a plain no rather than the
    undecidable pair above, where the sense might well apply and neither value can say. A caller
    that means to ask about both names both, because an entry genuinely sits in a folder *and* is
    of a type, and only the caller knows which type. See :meth:`look_up`.
    """
    if sense_scope.kind is ScopeKind.PROJECT:
        return True
    if sense_scope == query_scope:
        return True
    if sense_scope.kind is ScopeKind.FOLDER and query_scope.kind is ScopeKind.FOLDER:
        return _path_contains(sense_scope.value, query_scope.value)
    if sense_scope.kind is ScopeKind.ROOT and query_scope.kind is ScopeKind.FOLDER:
        message = (
            f"cannot decide whether a sense scoped to root {sense_scope.value!r} applies in "
            f"folder {query_scope.value!r}: a folder scope names a path relative to a root and "
            f"does not say which root, so containment is not computable from the scopes alone"
        )
        raise ValueError(message)
    return False


def _path_contains(outer: str | None, inner: str | None) -> bool:
    """Whether one canonical folder path contains another, or is it.

    Compared at a separator boundary, because ``plates/day1`` contains
    ``plates/day1/raw`` and does not contain ``plates/day10``. Paths are the
    census's ``relative_path`` form: forward slashes, no leading separator.
    """
    if outer is None or inner is None:
        return False
    return inner == outer or inner.startswith(f"{outer}/")


_LOOSE = re.compile(r"[^0-9a-z]+")


class Support(StrEnum):
    """How much the Dictionary contributes to a conclusion about one string.

    A level, not a number. The tier decides and a score only breaks ties, here as
    everywhere else in the engine, and a value that looked like a probability
    would be indefensible the first time anybody asked what it was calibrated
    against.
    """

    #: Nothing recognised. Says nothing about the file either way.
    NONE = "none"
    #: One distinct sense. Enough to propose, never enough to assert.
    WEAK = "weak"
    #: Two distinct senses agreeing on what a file is about.
    MODERATE = "moderate"
    #: Three or more. Still not proof: corroboration by in-corpus structure is
    #: what reaches the top tier, and that is the inference layer's job.
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class Sense:
    """What one term denotes, and everything known about the denoting."""

    term: str
    canonical: str
    theme_kind: str
    section: str
    surfaces: tuple[str, ...]
    origin: DictionaryOrigin = DictionaryOrigin.BUILTIN
    status: DictionaryStatus = DictionaryStatus.ACTIVE
    scope: Scope = PROJECT_SCOPE
    #: The field this sense belongs to, where it belongs to one. Absent for the
    #: universal sections, which is most of what matters on an unfamiliar corpus.
    domain: str | None = None
    #: Everything a particular consumer needs and the record schema does not
    #: carry: unit dimensions and conversion factors, how strongly a negative
    #: term signals doubt, whether an identifier is sensitive. Deliberately open,
    #: because constraining it would make every new consumer a schema change.
    properties: Mapping[str, Any] = field(default_factory=dict)
    ontology_ids: tuple[str, ...] = ()

    @property
    def declared_ambiguity(self) -> frozenset[str]:
        """Terms this sense is knowingly confusable with.

        A *declared* ambiguity is documentation: somebody looked at ``sim``,
        saw that it spells both a simulation and structured illumination
        microscopy, and recorded that nothing about the token distinguishes
        them. An *undeclared* one is a mistake, usually the same surface listed
        under two near-synonyms, and a test refuses it.
        """
        return frozenset(self.properties.get("ambiguous_with", ()) or ())

    @property
    def precedence(self) -> tuple[int, int]:
        """Return the ordering key deciding which sense wins a contested surface."""
        return (SCOPE_PRECEDENCE.get(self.scope.kind, 0), origin_rank(self))

    @property
    def digest(self) -> str:
        """Return a digest of everything that identifies this sense's content.

        The per-sense version the Dictionary otherwise lacks. The run record's
        ``dictionary_hash`` versions the whole composed vocabulary, which cannot
        answer whether *this* sense changed between two runs. This can, and it is
        what lets a binding written by an earlier run be compared against a later
        Dictionary without re-resolving anything.
        """
        return digest_text(
            canonical_json(
                {
                    "term": self.term,
                    "scope": self.scope.token,
                    "origin": self.origin.value,
                    "status": self.status.value,
                    "theme_kind": self.theme_kind,
                    "canonical": self.canonical,
                    "surfaces": list(self.surfaces),
                }
            )
        )

    def as_record(self, *, added_revision: int = 0) -> DictionaryEntry:
        """Return this sense as the persisted record type."""
        return DictionaryEntry(
            term_id=dictionary_term_id(term=self.term, scope=self.scope, origin=self.origin.value),
            term=self.term,
            surfaces=self.surfaces,
            theme_kind=self.theme_kind,
            canonical=self.canonical,
            scope=self.scope,
            origin=self.origin,
            status=self.status,
            evidence=f"section {self.section}",
            added_revision=added_revision,
            ontology_ids=self.ontology_ids,
            sense_digest=self.digest,
            section=self.section,
            domain=self.domain,
        )


@dataclass(frozen=True, slots=True)
class Section:
    """One shipped or installed file of senses."""

    name: str
    summary: str
    senses: tuple[Sense, ...]
    domain: str | None = None
    origin: DictionaryOrigin = DictionaryOrigin.BUILTIN
    #: The programs and packages the file declares by the parts they lay out side by side. They
    #: are apart from its senses because a layout binds no word: it is matched against a folder's
    #: children, and a spelling is what it exists not to match (:mod:`ingence.dictionary.programs`).
    layouts: tuple[ProgramLayout, ...] = ()
    #: Endings that form one new word with the capitals before them (the file's ``word_endings``
    #: list): ``ase`` in ``ATPase``. They are apart from the senses because an ending binds
    #: nothing. The tokeniser reads them where capitals meet lower case (:data:`WORD_ENDINGS_KEY`).
    word_endings: tuple[str, ...] = ()
    #: Endings after which a final ``s`` is never a plural's (the file's ``plural_keeps`` list):
    #: ``us``, ``is`` and ``ss`` end a Latin name or a singular (``Bacillus``, ``axis``, ``mass``),
    #: so a word ending in one is never read as the plural of the word without its ``s``
    #: (:meth:`DictionaryStore.look_up_plural`).
    plural_keeps: tuple[str, ...] = ()
    #: Prefixes that form one word with what follows them (the file's ``word_prefixes`` list):
    #: ``micro`` in ``micrograph``, ``meta`` in ``metaphase``. A run of letters is never cut after
    #: one into two Dictionary words.
    word_prefixes: tuple[str, ...] = ()
    #: The kinds a plural never folds onto (the file's ``plural_never_onto`` list): a unit, a date,
    #: a doubt word, a status. ``temps`` is not a temporary file, ``mays`` is not a month and
    #: ``bins`` is not an instruction to delete: a plural is a noun's, and those are not.
    plural_never_onto: tuple[str, ...] = ()
    #: Whether the section's senses bind words in names (the file's ``binds`` key, true unless it
    #: says ``false``). A section that does not bind is kept aside. The search reads its spellings
    #: (the question words a question is asked with). The binder, the themes and every reading of a
    #: name never see them, and the vocabulary's fingerprint and records leave them out, so no
    #: name's binding moves.
    binds: bool = True


@dataclass(frozen=True, slots=True)
class Contested:
    """One spelling two or more senses claim, and what the lookup does about it.

    Reported or decided, and the difference is everything. Senses of equal precedence tie: the
    lookup resolves to neither, the binding names both and a rule on that axis abstains and says
    why. Senses of unequal precedence do not tie: the higher wins outright, the lower is shadowed,
    and nothing is reported at all.
    """

    #: The spelling, as it is written.
    surface: str
    #: The sense precedence ranks first. Where :attr:`tied` is non-empty it is one of several
    #: standing level and is not a winner, which is why the outcome is read from :attr:`tied`
    #: rather than from this.
    winner: Sense
    #: The senses standing level with it. Non-empty means the collision is reported.
    tied: tuple[Sense, ...] = ()
    #: The senses that lost on precedence rather than tying, kept so what was passed over is
    #: nameable.
    shadowed: tuple[Sense, ...] = ()
    #: Whether this collision is one nobody had to declare or one somebody did. A collision across
    #: theme kinds is information rather than a mistake and needs no declaration. A collision
    #: within one kind is usually one idea entered twice and must be declared with
    #: ``ambiguous_with`` to count as deliberate. False means a collision within one kind that
    #: nobody declared, which is the only kind that is a defect.
    declared: bool = True

    @property
    def reported(self) -> bool:
        """Whether a lookup says the spelling is contested rather than quietly choosing."""
        return bool(self.tied)

    @property
    def senses(self) -> tuple[Sense, ...]:
        """Every sense claiming the spelling, best-ranked first."""
        return (self.winner, *self.tied, *self.shadowed)


@dataclass(frozen=True, slots=True)
class Hit:
    """One token recognised as one sense."""

    token: str
    sense: Sense
    quality: MatchQuality = MatchQuality.EXACT
    #: Other senses that matched equally well. Non-empty means the engine cannot tell which was
    #: meant, which is a fact worth carrying rather than resolving by arbitrary choice. A bare
    #: ``A`` is an ampere or an angstrom and nothing about the token says which.
    alternatives: tuple[Sense, ...] = ()
    #: Senses that matched and *lost* on precedence, rather than tying. A user entry shadows the
    #: shipped one it overrides, and the shipped one stays. If the lookup discarded it, a rule
    #: attenuating the user entry would have nothing to fall back to, and the shadowing would be
    #: one-directional and invisible. These are the losers, kept.
    shadowed: tuple[Sense, ...] = ()

    @property
    def ambiguous(self) -> bool:
        """Whether more than one sense matched with equal standing."""
        return bool(self.alternatives)


@dataclass(frozen=True, slots=True)
class Resolution:
    """What the Dictionary made of a whole string."""

    tokens: tuple[str, ...]
    hits: tuple[Hit, ...]

    @property
    def distinct_kinds(self) -> tuple[str, ...]:
        """Theme kinds recognised, in canonical order.

        The unit of compounding. Two techniques named in one filename are one
        kind of evidence, not two, which is the guard against a name that lists
        five synonyms scoring higher than one that names five different things.
        """
        return tuple(sorted({hit.sense.theme_kind for hit in self.hits}))

    @property
    def support(self) -> Support:
        """Return the level of support the Dictionary contributes."""
        count = len(self.distinct_kinds)
        if count == 0:
            return Support.NONE
        if count == 1:
            return Support.WEAK
        if count == 2:
            return Support.MODERATE
        return Support.STRONG

    @property
    def tiebreak(self) -> float:
        """Order within a support level. Not a probability and not comparable across levels.

        Exact matches count fully and loose ones count less, so two files at the
        same level are ordered by how cleanly they matched.
        """
        weights = {
            MatchQuality.EXACT: 1.0,
            MatchQuality.CASE_FOLDED: 0.75,
            MatchQuality.LOOSE: 0.5,
        }
        return sum(weights[hit.quality] * (0.5 if hit.ambiguous else 1.0) for hit in self.hits)


def _loose_key(text: str) -> str:
    """Return the punctuation-free, case-free form used for fallback matching."""
    return _LOOSE.sub("", text.casefold())


def load_sections(
    directory: Path = BUILTIN_DIRECTORY,
    *,
    origin: DictionaryOrigin = DictionaryOrigin.BUILTIN,
    scope: Scope = PROJECT_SCOPE,
) -> tuple[Section, ...]:
    """Load every section under a directory, including domain subdirectories.

    YAML rather than anything faster to parse, because these files are meant to
    be read and edited by people. A user who disagrees with a sense should be
    able to open the file, see why it is there, and change it; that is worth more
    than the milliseconds a binary format would save loading a few thousand
    entries once per run.
    """
    sections: list[Section] = []
    for path in sorted(directory.rglob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "section" not in document and "entries" not in document:
            # A list beside the sections (the spelling deny-list), not a section.
            continue
        sections.append(_section_from(document, origin=origin, scope=scope))
    return tuple(sections)


#: English words one edit from a shipped spelling, never read as a slip of it. The list is built
#: offline into this file from a hand-written list of common English words against the shipped
#: Dictionary (``strain`` beside ``stain``, ``slice`` beside ``slide``).
SPELLING_DENYLIST_FILE = BUILTIN_DIRECTORY / "spelling_denylist.yaml"


@lru_cache(maxsize=1)
def spelling_denylist() -> frozenset[str]:
    """The words the shipped deny-list holds, casefolded; empty where the file is absent."""
    if not SPELLING_DENYLIST_FILE.is_file():
        return frozenset()
    document = yaml.safe_load(SPELLING_DENYLIST_FILE.read_text(encoding="utf-8")) or {}
    listed = document.get("spelling_denylist") or ()
    return frozenset(str(word).casefold() for word in listed if word)


#: The kinds a slip never folds onto: the ones a plural never does (a unit, a date, a doubt word or
#: a status, listed under ``plural_never_onto``) and a format or a version word besides.
CORRECTION_NEVER_ONTO = frozenset({"format", "version"})


def _extends(sense: Sense) -> bool:
    """Whether this entry adds spellings to a sense loaded elsewhere (:data:`EXTENDS`)."""
    return sense.properties.get(EXTENDS) is True


def _without_extends(sense: Sense) -> Sense:
    """Return the sense with the flag taken out, which has done its work once folding ran."""
    return replace(
        sense, properties={key: value for key, value in sense.properties.items() if key != EXTENDS}
    )


def _merged_property(key: str, kept: object, added: object, *, where: str) -> object:
    """Return one property of a sense and an extension of it, or refuse a contradiction.

    Lists join and maps join, and nothing is overwritten. A list (``extension_only``,
    ``ambiguous_with``) keeps the sense's own items first and adds what the extension adds. A map
    (``origins``) takes the extension's keys beside the sense's. A value that both declare must
    agree, because an extension adds spellings and does not re-decide what the sense already says.
    A pack that disagrees with the core about who writes a spelling is a mistake in one of them,
    and folding it quietly would make the answer depend on which loaded last.
    """
    if isinstance(kept, list | tuple) and isinstance(added, list | tuple):
        return [*kept, *(item for item in added if item not in kept)]
    if isinstance(kept, dict) and isinstance(added, dict):
        clashing = sorted(
            str(item) for item in kept.keys() & added.keys() if kept[item] != added[item]
        )
        if clashing:
            message = f"{where} declares {key} for {', '.join(clashing)} differently"
            raise ValueError(message)
        return {**kept, **added}
    if kept == added:
        return kept
    message = f"{where} declares {key} as {added!r} where the sense it extends has {kept!r}"
    raise ValueError(message)


def _extended(sense: Sense, extension: Sense) -> Sense:
    """Return one sense with an extension's spellings and properties folded into it.

    The sense's own spellings stay first, then the extension's in the order it wrote them, so the
    core and a pack loaded together give the sense written whole to the character, its digest and
    identifier included. The kind, the canonical name, the section and the domain are the sense's:
    an extension adds spellings to a type and cannot re-file it.
    """
    where = f"the {extension.section} entry extending {sense.term}"
    if extension.theme_kind != sense.theme_kind:
        message = (
            f"{where} is a {extension.theme_kind}, and the {sense.section} sense it extends is "
            f"a {sense.theme_kind}"
        )
        raise ValueError(message)
    if extension.canonical not in {extension.term, sense.canonical}:
        message = f"{where} renames it {extension.canonical!r}; an extension adds spellings only"
        raise ValueError(message)
    properties = dict(sense.properties)
    for key, value in extension.properties.items():
        if key == EXTENDS:
            continue
        properties[key] = (
            _merged_property(key, properties[key], value, where=where)
            if key in properties
            else value
        )
    return replace(
        sense,
        surfaces=(
            *sense.surfaces,
            *(surface for surface in extension.surfaces if surface not in sense.surfaces),
        ),
        properties=properties,
        ontology_ids=(
            *sense.ontology_ids,
            *(item for item in extension.ontology_ids if item not in sense.ontology_ids),
        ),
    )


def fold_extensions(sections: Iterable[Section]) -> tuple[Section, ...]:
    """Return the sections with every extension folded into the sense it extends (:data:`EXTENDS`).

    An extension lands on the sense of its term, origin and scope, wherever that sense's section
    sits in the load order. The domain packs load ahead of the core, so the microscopy pack's image
    spellings are read before the core's image type and still join it. Where two senses share a
    term, origin and scope, the later one is the one extended, as it is the one
    :meth:`DictionaryStore.sense_for_term` answers with. Several extensions of one sense join it in
    load order.

    An extension whose sense is not loaded stands as a sense of its own, with its flag taken out.
    Its spellings still bind to the type its term names, which is what its author wrote. A
    laboratory's pack loads at the pack origin, so it never folds into a shipped sense.

    Folding happens where a store is built from its sections, so no store ever holds half a type.
    A section added afterwards with :meth:`DictionaryStore.add` is indexed as written.
    """
    listed = list(sections)
    targets: dict[tuple[str, str, str], tuple[int, int]] = {}
    for at, section in enumerate(listed):
        for place, sense in enumerate(section.senses):
            if not _extends(sense):
                targets[(sense.term, sense.origin.value, sense.scope.token)] = (at, place)
    senses = [list(section.senses) for section in listed]
    folded: set[tuple[int, int]] = set()
    for at, section in enumerate(listed):
        for place, sense in enumerate(section.senses):
            if not _extends(sense):
                continue
            target = targets.get((sense.term, sense.origin.value, sense.scope.token))
            if target is None:
                senses[at][place] = _without_extends(sense)
                continue
            senses[target[0]][target[1]] = _extended(senses[target[0]][target[1]], sense)
            folded.add((at, place))
    return tuple(
        replace(
            section,
            senses=tuple(
                sense for place, sense in enumerate(senses[at]) if (at, place) not in folded
            ),
        )
        for at, section in enumerate(listed)
    )


def load_shipped_sections() -> tuple[Section, ...]:
    """Load everything that ships: the domain sections, then the universal ones.

    The domain sections load first, ahead of every universal file. The order matters: where two
    senses share a term, the later one is the one :func:`fold_extensions` extends and
    :meth:`DictionaryStore.sense_for_term` answers with. Projects read by earlier versions used
    this order, so it keeps their bindings. Both stand at the core's origin, level. A domain
    section's senses carry its domain, which the resolver reads a shared spelling by
    (:class:`~ingence.dictionary.discover.DomainBinder`).
    """
    return (*load_sections(PACKS_DIRECTORY), *load_sections(BUILTIN_DIRECTORY))


def _section_from(
    document: Mapping[str, Any], *, origin: DictionaryOrigin, scope: Scope
) -> Section:
    """Build one section from a parsed YAML document."""
    name = str(document.get("section", "unnamed"))
    domain = document.get("domain")
    default_kind = str(document.get("theme_kind", "term"))
    senses: list[Sense] = []

    # A section's format senses come from the file-type table where it says so:
    # ``file_types: from_table`` adds the table's entries for this section after the file's own, as
    # the file would have declared them.
    entries = list(document.get("entries", ()) or ())
    if document.get(FILE_TYPES_KEY) is not None:
        entries.extend(_table_entries(document[FILE_TYPES_KEY], section=name, origin=origin))

    for raw in entries:
        term = str(raw["term"])
        surfaces = tuple(str(surface) for surface in raw.get("surfaces", ()) or ())
        # Reserved keys become fields; everything else is a property. New keys in
        # a section file therefore reach their consumer without any code change,
        # which is what lets a unit table and a doubt lexicon share one loader.
        properties = {
            key: value
            for key, value in raw.items()
            if key
            not in {"term", "surfaces", "canonical", "theme_kind", "ontology_ids", FILE_TYPE_KEY}
        }
        # A sense declared for one type of file is a reading only in a file of that type, named by
        # the type's term (``read_format``).
        of_type = raw.get(FILE_TYPE_KEY)
        senses.append(
            Sense(
                term=term,
                canonical=str(raw.get("canonical", term)),
                theme_kind=str(raw.get("theme_kind", default_kind)),
                section=name,
                surfaces=surfaces or (term,),
                origin=origin,
                scope=Scope(ScopeKind.FILE_TYPE, str(of_type)) if of_type else scope,
                domain=str(domain) if domain else None,
                properties=properties,
                ontology_ids=tuple(raw.get("ontology_ids", ()) or ()),
            )
        )

    return Section(
        name=name,
        summary=str(document.get("summary", "")),
        senses=tuple(senses),
        domain=str(domain) if domain else None,
        origin=origin,
        layouts=layouts_from(document.get(LAYOUTS_KEY), section=name),
        word_endings=_word_endings_from(document.get(WORD_ENDINGS_KEY), section=name),
        plural_keeps=_word_endings_from(document.get(PLURAL_KEEPS_KEY), section=name),
        word_prefixes=_word_endings_from(document.get(WORD_PREFIXES_KEY), section=name),
        plural_never_onto=_kinds_from(document.get(PLURAL_NEVER_ONTO_KEY), section=name),
        binds=document.get(BINDS_KEY, True) is not False,
    )


def _table_entries(
    declared: object, *, section: str, origin: DictionaryOrigin
) -> list[dict[str, Any]]:
    """Return the file-type table's entries for one shipped section, or refuse in words.

    Only a shipped section takes its formats from the table: a laboratory's pack loads at the
    pack origin and declares its own. A section the table has no row for is a misspelling, and
    saying so beats a section that silently binds nothing.
    """
    from ingence.dictionary.file_types import FROM_TABLE, file_type_table

    if declared != FROM_TABLE:
        message = f"{section}: {FILE_TYPES_KEY} is {FROM_TABLE!r} or absent, not {declared!r}"
        raise ValueError(message)
    if origin is not DictionaryOrigin.BUILTIN:
        message = f"{section}: only a shipped section takes its formats from the file-type table"
        raise ValueError(message)
    entries = file_type_table().format_entries(section)
    if not entries:
        message = f"{section}: the file-type table has no row naming this section"
        raise ValueError(message)
    return entries


#: The key a shipped section file sets to ``from_table`` to say its format senses are the
#: file-type table's rows naming it. It is the same name :mod:`ingence.dictionary.file_types`
#: declares, held here so this module reads a document without importing that one.
FILE_TYPES_KEY = "file_types"

#: The key an entry names the one type of file it is a reading in under: the type's Dictionary
#: term, so the sense's scope is that file type (``ScopeKind.FILE_TYPE``).
FILE_TYPE_KEY = "file_type"

#: The key a section file lists its word-forming endings under, beside its ``entries``.
WORD_ENDINGS_KEY = "word_endings"

#: The key a section file lists the endings under after which a final ``s`` is no plural's.
PLURAL_KEEPS_KEY = "plural_keeps"

#: The key a section file lists the prefixes under that form one word with what follows them.
WORD_PREFIXES_KEY = "word_prefixes"

#: The key a section file lists the kinds under that a plural never folds onto.
#: A section file's key saying its senses bind no word in a name.
BINDS_KEY = "binds"
PLURAL_NEVER_ONTO_KEY = "plural_never_onto"

#: The shortest word read as a plural, by default (the setting ``dictionary.plural_minimum``):
#: four letters. A three-letter word ending in ``s`` is a unit (``ms``, ``ns`` are two) or an
#: abbreviation far more often than the plural of a two-letter word, and no shipped two-letter
#: spelling is a word a plural is made from.
PLURAL_MINIMUM = 4


def _kinds_from(raw: object, *, section: str) -> tuple[str, ...]:
    """Read a section file's list of kinds: lower-case words joined by underscores."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item.replace("_", "").isalpha() and item.islower() for item in raw
    ):
        message = f"{section}'s kinds are lower-case words in a list, not {raw!r}"
        raise ValueError(message)
    return tuple(sorted(set(raw)))


def _declared_for_a_type(sense: Sense) -> bool:
    """Whether the vocabulary itself declared a sense for one type of file (``file_type``)."""
    return sense.scope.kind is ScopeKind.FILE_TYPE and sense.origin is not DictionaryOrigin.USER


def singulars_of(word: str) -> tuple[str, ...]:
    """Return the singulars an English plural may be written from, by its grammar alone.

    ``-ies`` from ``-y`` (``flies``), ``-es`` after a sibilant from the word without it
    (``boxes``, ``masses``) or with only the ``s`` dropped (``phases``), and any other final ``s``
    dropped (``masks``). Casefolded; empty for a word no plural rule reads. Whether a singular is a
    word is the Dictionary's to say (:meth:`DictionaryStore.look_up_plural`), never this
    function's.
    """
    folded = word.casefold()
    if not folded.endswith("s"):
        return ()
    found: list[str] = []
    if folded.endswith("ies"):
        found.append(folded[:-3] + "y")
    found.append(folded[:-1])
    if folded.endswith(("ses", "xes", "zes", "ches", "shes")):
        found.append(folded[:-2])
    return tuple(dict.fromkeys(found))


def _word_endings_from(raw: object, *, section: str) -> tuple[str, ...]:
    """Read a section file's ``word_endings``: lower-case letters only, refused otherwise."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item.isalpha() and item.islower() for item in raw
    ):
        message = f"{section}'s word_endings are lower-case letters in a list, not {raw!r}"
        raise ValueError(message)
    return tuple(sorted(set(raw)))


class DictionaryStore:
    """Scoped lookup over every loaded section."""

    #: The laboratory packs a project's store loaded, by name and in order, which a run records
    #: beside the fingerprint. Empty for the shipped vocabulary alone.
    packs: tuple[InstalledPack, ...] = ()
    #: The vocabulary as composed before a person's inactivations, where a project's Record
    #: inactivates anything. It is empty otherwise, since the senses are then that vocabulary. It
    #: is read only for a C-library time stamp's weekday and month words
    #: (:meth:`senses_before_inactivation`).
    before_inactivation: tuple[Section, ...] = ()

    def __init__(self, sections: Iterable[Section] = ()) -> None:
        self._sections: list[Section] = []
        # Three indexes, consulted in order. Separate rather than one folded
        # index, because folding case is safe for words and destructive for unit
        # symbols, and the store cannot know which a token is until it matches.
        self._exact: dict[str, list[Sense]] = {}
        self._folded: dict[str, list[Sense]] = {}
        self._loose: dict[str, list[Sense]] = {}
        # Term to sense, for a consumer that already knows which term it has and
        # needs the rest of the sense. The normaliser's unit table resolves a
        # unit by its own lookup, so a second surface lookup to recover the term
        # identifier could land on a different sense entirely.
        self._by_term: dict[str, Sense] = {}
        #: The spellings indexed for :meth:`correct`, built on first use.
        self._spellings: SpellingIndex | None = None
        #: The pairs of spellings a person called unrelated (``distinct:<a>+<b>``, a rejection in
        #: the answer log), each casefolded and sorted. A correction between the two is never made.
        #: :meth:`for_project` sets it.
        self.distinct: frozenset[tuple[str, str]] = frozenset()
        #: The Dictionary spelling each corrected word was read as, by the word casefolded. The
        #: :meth:`correct` method fills it, and it gives what the binding's marker and the finding
        #: say.
        self.corrected_to: dict[str, str] = {}
        #: Term identifiers, computed once. A term identifier is a hash over a
        #: canonically serialised payload, and a pool of 25,000 quantity-dense
        #: names asks for one 44,000 times across 620 distinct terms.
        #:
        #: The key is the term and the scope, because the identifier is a function of both. Keyed by
        #: term alone, two senses of one term at different scopes would return whichever was
        #: computed first, and which one that is depends on the order the filesystem handed back
        #: names. The problem is dormant only because every shipped sense is project-scoped. It
        #: would fire the day a folder-scoped section exists, and a conclusion could not be made
        #: again from the same inputs.
        self._term_ids: dict[tuple[str, str, str], str] = {}
        #: Whether every sense loaded so far is project-scoped, which every
        #: shipped section is. While it holds, :func:`scope_applies` would answer
        #: true for every candidate at every query scope, so the filter is skipped
        #: rather than run 44,000 times to reach the same set. Cleared for good by
        #: the first narrower sense, never recomputed, so it cannot go stale in
        #: the direction that would drop a candidate wrongly.
        self._all_project_scoped = True
        #: Sections that bind nothing (``binds: false``), by name: read by the search alone.
        self._aside: dict[str, Section] = {}
        # A type whose spellings arrive in two sections is one sense here: the store folds the
        # core's half and a pack's before it indexes anything.
        for section in fold_extensions(sections):
            self.add(section)

    def aside(self, name: str) -> Section | None:
        """A section kept aside from binding (``binds: false``), by name, or None."""
        return self._aside.get(name)

    def aside_surfaces(self, name: str) -> frozenset[str]:
        """Every spelling of a section kept aside, casefolded: what the search drops or reads
        from it without any name's binding being touched."""
        section = self._aside.get(name)
        if section is None:
            return frozenset()
        return frozenset(
            surface.casefold() for sense in section.senses for surface in sense.surfaces
        )

    # -- loading ----------------------------------------------------------

    def add(self, section: Section) -> None:
        """Index one section's senses; a section that does not bind is kept aside, unindexed."""
        if not section.binds:
            self._aside[section.name] = section
            return
        self._sections.append(section)
        for sense in section.senses:
            if sense.scope.kind is not ScopeKind.PROJECT:
                self._all_project_scoped = False
            # Later sections win a contested term, which is the same precedence
            # a contested surface follows: a user override shadows what it
            # overrides rather than sitting beside it.
            self._by_term[sense.term] = sense
            for surface in sense.surfaces:
                self._exact.setdefault(surface, []).append(sense)
                folded = surface.casefold()
                if folded != surface:
                    self._folded.setdefault(folded, []).append(sense)
                else:
                    self._folded.setdefault(folded, []).append(sense)
                loose = _loose_key(surface)
                if loose and loose != folded:
                    self._loose.setdefault(loose, []).append(sense)

    @staticmethod
    def builtin() -> DictionaryStore:
        """Return a store loaded with everything that ships."""
        return DictionaryStore(load_shipped_sections())

    @staticmethod
    def for_project(project: Path) -> DictionaryStore:
        """Return what ships, with what the project's Record says a person decided.

        The Dictionary a run consults is the whole shipped vocabulary plus a person's answers.
        Every shipped section loads, the domain sections level with the universal ones, except a
        domain that a person vetoed (a refusal on ``pack:<domain>`` in the answer log,
        :func:`~ingence.dictionary.packs.proposals.vetoed_domains`). A laboratory's own packs load
        as the settings name them. Every answer is a ``USER`` sense at precedence 40, which
        outranks everything shipped, each at the scope its own answer carried. A project whose
        Record holds no answer and no veto gets exactly :meth:`builtin`.

        Imported inside, because the module that builds the senses reads this one.
        """
        from ingence.core.project import Project
        from ingence.dictionary.answers import answered_sections
        from ingence.dictionary.inactive import inactivations_in_force, without_inactive
        from ingence.dictionary.laboratory_packs import load_named_packs
        from ingence.dictionary.packs.proposals import vetoed_domains
        from ingence.dictionary.variants import distinct_pairs
        from ingence.storage.layout import PROJECT_FILE

        settings = (
            Project.open(project).settings.dictionary
            if (project / PROJECT_FILE).is_file()
            else None
        )
        named = settings.packs if settings is not None else ()
        packs, sections = load_named_packs(project, named) if named else ((), ())
        vetoed = vetoed_domains(project) if settings is not None else frozenset()
        shipped = tuple(
            section
            for section in load_shipped_sections()
            if not section.domain or section.domain not in vetoed
        )
        # What a person inactivated: a sense left out, a spelling taken out of its sense. The
        # store reads it from the Record beside the answers and leaves the shipped files untouched.
        inactive = inactivations_in_force(project)
        composed = (*shipped, *sections, *answered_sections(project))
        store = DictionaryStore(without_inactive(composed, inactive))
        store.packs = packs
        # The pairs a person called unrelated: a run reads them, so a rejected correction does not
        # come back at the next reading.
        store.distinct = distinct_pairs(project)
        if inactive:
            # Folded as the store's own sections are, so an extension's spellings read the same.
            store.before_inactivation = fold_extensions(composed)
        return store

    # -- description ------------------------------------------------------

    @property
    def sections(self) -> tuple[Section, ...]:
        return tuple(self._sections)

    def __len__(self) -> int:
        return sum(len(section.senses) for section in self._sections)

    @property
    def surface_count(self) -> int:
        """How many distinct spellings are recognised."""
        return len(self._exact) + len(self._folded) + len(self._loose)

    def sense_for_term(self, term: str) -> Sense | None:
        """Return the sense a term names, or None."""
        return self._by_term.get(term)

    def term_id(self, sense: Sense) -> str:
        """Return a sense's persisted identifier, computed once per term and scope."""
        key = (sense.term, sense.scope.token, sense.origin.value)
        found = self._term_ids.get(key)
        if found is None:
            found = dictionary_term_id(
                term=sense.term, scope=sense.scope, origin=sense.origin.value
            )
            self._term_ids[key] = found
        return found

    def senses(self) -> Iterator[Sense]:
        """Yield every sense, in canonical order."""
        for section in sorted(self._sections, key=lambda item: item.name):
            yield from sorted(section.senses, key=lambda sense: sense.term)

    def word_endings(self) -> frozenset[str]:
        """Every ending a section declares as forming one new word with the capitals before it."""
        return frozenset(ending for section in self._sections for ending in section.word_endings)

    def word_prefixes(self) -> frozenset[str]:
        """Every prefix a section declares as forming one word with what follows it."""
        return frozenset(prefix for section in self._sections for prefix in section.word_prefixes)

    def plural_excluded_kinds(self) -> frozenset[str]:
        """Every kind a section declares a plural never folds onto (``unit``, ``date`` ...)."""
        return frozenset(kind for section in self._sections for kind in section.plural_never_onto)

    def plural_keeps(self) -> frozenset[str]:
        """Every ending a section declares as keeping its final ``s`` (``us``, ``is``, ``ss``)."""
        return frozenset(ending for section in self._sections for ending in section.plural_keeps)

    def correct(
        self,
        token: str,
        *,
        scope: Scope | Sequence[Scope] = PROJECT_SCOPE,
    ) -> Hit | None:
        """Return the one Dictionary spelling that a word is a slip of, or None.

        The method is asked only where the word as written and as a plural found nothing. A word of
        letters only is bound to a unique candidate: at least ``CORRECTION_ONE_EDIT_LETTERS``
        letters and one edit away, or at least ``CORRECTION_TWO_EDIT_LETTERS`` letters and two edits
        away with the same letters in another order (both limits are in
        :mod:`ingence.dictionary.spelling`). The method refuses in these cases:

        - a second candidate stands within one extra edit;
        - the word is all capitals at five letters or fewer (an acronym);
        - the candidate spells a unit, a date, a doubt word, a status, a format or a version;
        - the word is in the shipped deny-list of English words one edit from a spelling
          (:func:`spelling_denylist`);
        - a person said the two are unrelated (``distinct:<a>+<b>`` in the answer log,
          :attr:`distinct`).

        The hit carries :attr:`~ingence.core.schema.MatchQuality.CORRECTED`, so a rule whose floor
        is exact or loose refuses it by the order.
        """
        from ingence.dictionary.spelling import (
            CORRECTION_ONE_EDIT_LETTERS,
            CORRECTION_TWO_EDIT_LETTERS,
        )

        folded = token.casefold()
        if not folded.isalpha() or len(folded) < CORRECTION_ONE_EDIT_LETTERS:
            return None
        if token.isupper() and len(folded) <= CORRECTION_ONE_EDIT_LETTERS:
            return None
        if folded in spelling_denylist():
            return None
        candidates = self._spelling_index().candidates(token)
        if not candidates:
            return None
        first = candidates[0]
        if len(candidates) > 1 and candidates[1].edits <= first.edits + 1:
            return None
        # Two edits only with the same letters in another order: ``hoescht`` for ``hoechst``. The
        # same first two and last letters admitted too many English words.
        if first.edits == 2 and (
            len(folded) < CORRECTION_TWO_EDIT_LETTERS or sorted(folded) != sorted(first.spelling)
        ):
            return None
        if first.edits > 2:
            return None
        pair = tuple(sorted((folded, first.spelling)))
        if pair in self.distinct:
            return None
        excluded = self.plural_excluded_kinds() | CORRECTION_NEVER_ONTO
        if self.look_up(first.spelling, permitted_kinds=excluded) is not None:
            return None
        hit = self.look_up(first.spelling, scope=scope, excluded_kinds=excluded)
        if hit is None:
            return None
        self.corrected_to[folded] = first.spelling
        return replace(hit, token=token, quality=MatchQuality.CORRECTED)

    def _spelling_index(self) -> SpellingIndex:
        """The spellings of letters alone, five letters or more, indexed once per store."""
        from ingence.dictionary.spelling import CORRECTION_ONE_EDIT_LETTERS, SpellingIndex

        if self._spellings is None:
            self._spellings = SpellingIndex(
                (
                    (sense.term, surface)
                    for sense in self.senses()
                    if sense.status is DictionaryStatus.ACTIVE
                    for surface in sense.surfaces
                ),
                minimum=CORRECTION_ONE_EDIT_LETTERS,
                edits=2,
                candidates=8,
            )
        return self._spellings

    def look_up_plural(
        self,
        token: str,
        *,
        scope: Scope | Sequence[Scope] = PROJECT_SCOPE,
        minimum: int = PLURAL_MINIMUM,
    ) -> Hit | None:
        """Return a plural's singular, where the singular is a Dictionary word, or None.

        The method is asked only where the word as written found nothing. ``masks`` reads as
        ``mask`` and ``flies`` as ``fly``. ``zephs`` is nothing, because ``zeph`` is not a word. A
        Latin name never loses its final s: a word ending in an ending that the Dictionary lists as
        keeping it (:meth:`plural_keeps`: ``Bacillus``, ``axis``) is never read as a plural. A genus
        or a species word (``Pseudomonas``, ``elegans``) folds onto nothing because its stem is no
        word. A word shorter than ``minimum`` letters, or not of letters alone, is never a plural,
        nor is a word in capitals whose final S is a capital (``SIMS``, an acronym). A plural never
        folds onto a kind that the Dictionary lists (:meth:`plural_excluded_kinds`: a unit, a date,
        a doubt word, a status), nor onto a spelling that one of them holds, even where it spells
        another sense too (``mins`` is no plural of the statistic ``min``). The singular is matched
        as written or ignoring case, never loosely, and the hit carries
        :attr:`~ingence.core.schema.MatchQuality.LOOSE`: the name did not write that spelling.
        """
        folded = token.casefold()
        if len(folded) < minimum or not folded.isalpha() or token.isupper():
            return None
        excluded = self.plural_excluded_kinds()
        if folded.endswith(tuple(self.plural_keeps())):
            return None
        for singular in singulars_of(folded):
            if (
                self.look_up(
                    singular,
                    permitted_kinds=excluded,
                    minimum_quality=MatchQuality.CASE_FOLDED,
                )
                is not None
            ):
                # A unit's or a date's spelling (``min``, ``may``) is never folded onto, whatever
                # else it spells.
                continue
            hit = self.look_up(
                singular,
                scope=scope,
                excluded_kinds=excluded,
                minimum_quality=MatchQuality.CASE_FOLDED,
            )
            if hit is not None:
                return replace(hit, token=token, quality=MatchQuality.LOOSE)
        return None

    def senses_before_inactivation(self) -> Iterator[Sense]:
        """Yield every sense as composed before a person's inactivations, in canonical order.

        A C-library time stamp's words are the C library's. ``ctime`` writes the same weekday and
        month abbreviations whatever a laboratory's names mean by them, so a person inactivating a
        weekday or a month as a word leaves a stamp reading as one. Everything else reads
        :meth:`senses`.
        """
        if not self.before_inactivation:
            yield from self.senses()
            return
        for section in sorted(self.before_inactivation, key=lambda item: item.name):
            yield from sorted(section.senses, key=lambda sense: sense.term)

    # -- lookup -----------------------------------------------------------

    def look_up(
        self,
        token: str,
        *,
        scope: Scope | Sequence[Scope] = PROJECT_SCOPE,
        permitted_kinds: frozenset[str] | None = None,
        excluded_kinds: frozenset[str] | None = None,
        minimum_quality: MatchQuality = MATCH_QUALITY_ORDER[-1],
    ) -> Hit | None:
        """Return the winning sense for one token, or None.

        Contested surfaces are resolved by precedence: narrowest scope first,
        then origin. An exact match always beats a loose one, because stripping
        punctuation to match is a guess and matching as written is not.

        ``permitted_kinds`` restricts the candidate set before precedence runs, which is what lets
        position constrain meaning. A token in an extension slot can only be a format, so ``md``
        there resolves to markdown rather than to molecular dynamics, and the restriction states
        what may bind rather than guessing what probably means what. Filtering before ``_winner``
        rather than after is deliberate: filtering after would let an excluded sense win and then
        abstain, reporting a conflict where there is none.

        ``excluded_kinds`` removes kinds the same way, where a position rules out one kind and
        admits every other: a one-character word may be a statistic or an initial, and is never a
        unit symbol.

        ``minimum_quality`` restricts how the token may be *spelled* rather than what it may mean,
        which is the other axis a position constrains. The indexes weaker than it are never
        consulted, so a token admitted at :data:`~ingence.core.schema.MatchQuality.EXACT` reaches
        only a surface written exactly as it is. A bare number is looked up that way: ``405`` is a
        laser line because a sense spells it ``405``, and ``2`` is nothing, because no shipped
        surface is a single digit and reaching ``_2`` or ``(2)`` means stripping the punctuation
        that made it an index.

        ``scope`` restricts the candidates the same way and for the same reason. A sense declared
        for one folder is not a candidate for a question asked about another, and dropping it
        before ``_winner`` keeps precedence ranking only senses that could legitimately have won.
        See :func:`scope_applies` for which scopes reach which.

        A question may name more than one place, and one entry always does. A file sits in a
        folder, under a root, and is of a type, and a sense scoped to any of them answers a
        question asked about that entry. Several places widen what is admitted and change nothing
        about precedence, which still ranks by each sense's own scope: a folder-scoped sense beats
        a type-scoped one on a file that is in both, because the narrower statement wins wherever
        both apply (:data:`SCOPE_PRECEDENCE`).
        """
        asked = (scope,) if isinstance(scope, Scope) else tuple(scope) or (PROJECT_SCOPE,)
        admitted = MATCH_QUALITY_ORDER[: MATCH_QUALITY_ORDER.index(minimum_quality) + 1]
        for index, quality in (
            (self._exact, MatchQuality.EXACT),
            (self._folded, MatchQuality.CASE_FOLDED),
            (self._loose, MatchQuality.LOOSE),
        ):
            if quality not in admitted:
                continue
            key = (
                token
                if quality is MatchQuality.EXACT
                else (
                    token.casefold() if quality is MatchQuality.CASE_FOLDED else _loose_key(token)
                )
            )
            candidates = index.get(key)
            if permitted_kinds is not None:
                candidates = [
                    sense for sense in (candidates or ()) if sense.theme_kind in permitted_kinds
                ]
            if excluded_kinds:
                candidates = [
                    sense for sense in (candidates or ()) if sense.theme_kind not in excluded_kinds
                ]
            if candidates is not None:
                candidates = [
                    sense
                    for sense in candidates
                    if (permitted_kinds is not None and sense.theme_kind in permitted_kinds)
                    or _binds_outside_an_extension(sense, token)
                ]
            if candidates is not None and not self._all_project_scoped:
                candidates = [
                    sense
                    for sense in candidates
                    if any(scope_applies(sense.scope, place) for place in asked)
                ]
            if candidates:
                winner, alternatives, shadowed = self._winner(candidates)
                return Hit(
                    token=token,
                    sense=winner,
                    quality=quality,
                    alternatives=alternatives,
                    shadowed=shadowed,
                )
        return None

    @staticmethod
    def _winner(
        candidates: Sequence[Sense],
    ) -> tuple[Sense, tuple[Sense, ...], tuple[Sense, ...]]:
        """Return the winning sense and any that tied with it.

        A tie is not resolved by picking. Precedence settles a *deliberate*
        override, where a user has said what a word means here; where two senses
        stand equal, nothing about the token distinguishes them and pretending
        otherwise would invent a fact. The alternatives travel with the hit so a
        rule can require an unambiguous match, or abstain.

        The senses that *lost* travel with it too. If the lookup discarded them, attenuation would
        be one-directional: a rule could refuse to trust a user override but have nothing to fall
        back to, because the entry it shadowed would be gone by the time anything downstream could
        ask.
        """
        ranked = sorted(candidates, key=lambda sense: sense.precedence, reverse=True)
        winner = ranked[0]
        tied = tuple(
            sense
            for sense in ranked[1:]
            if sense.precedence == winner.precedence and sense.term != winner.term
        )
        lost = tuple(sense for sense in ranked[1:] if sense.precedence != winner.precedence)
        return winner, tied, lost

    def resolve(
        self, tokens: Sequence[str], *, scope: Scope | Sequence[Scope] = PROJECT_SCOPE
    ) -> Resolution:
        """Recognise what can be recognised in a sequence of tokens."""
        hits = [
            hit for hit in (self.look_up(token, scope=scope) for token in tokens) if hit is not None
        ]
        return Resolution(tokens=tuple(tokens), hits=tuple(hits))

    def collisions(self) -> tuple[Contested, ...]:
        """Return every contested spelling and what the lookup does about it.

        :meth:`undeclared_ambiguities` says which contested spellings nobody declared. This method
        says which are *reported* and which are *decided*. A collision between senses of equal
        precedence ties and resolves to neither. A collision between unequal ones is won outright
        with the loser shadowed and nothing said.

        A decided collision is the one to look at. It is the engine answering a question on
        somebody's behalf without mentioning that there was a question, which is what a pack above
        the core would do to all 37 of the shipped vocabulary's core-to-domain ambiguities.

        The method ranks by the one place precedence is applied, :meth:`_winner`, so this audit and
        every lookup cannot disagree about who wins.

        Unscoped, because it is an audit of the vocabulary rather than of a question asked
        somewhere: which senses *could* contest a spelling, not which ones do in one folder.
        """
        # From the one method that decides what needs declaring, rather than from a second
        # reading of the same rule. A collision across theme kinds is information rather than a
        # mistake (a spinning disk and a standard deviation really do both spell ``sd``), and only
        # a collision within one kind has to be declared.
        undeclared = {surface for surface, _ in self.undeclared_ambiguities()}
        found: list[Contested] = []
        for surface, every in sorted(self._exact.items()):
            # A sense that the vocabulary declares for one type of file contests nothing here. It
            # is a reading only in a file of that type, where it is the narrower statement and wins
            # as any narrower one does. This audit is of the project.
            candidates = [sense for sense in every if not _declared_for_a_type(sense)]
            if len({sense.term for sense in candidates}) < 2:
                continue
            winner, tied, shadowed = self._winner(candidates)
            found.append(
                Contested(
                    surface=surface,
                    winner=winner,
                    tied=tied,
                    shadowed=shadowed,
                    declared=surface not in undeclared,
                )
            )
        return tuple(found)

    def undeclared_ambiguities(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return collisions that nobody declared, which are mistakes.

        Two senses of the *same* theme kind sharing a spelling are usually one
        idea entered twice under near-synonyms: the same evidence counted as two
        terms. Where the collision is real, declaring it in the section file with
        ``ambiguous_with`` turns it from a mistake into documentation.

        Collisions across theme kinds are left alone. A spinning disk and a
        standard deviation really do both spell ``sd``, and that is information
        rather than an error.
        """
        found: list[tuple[str, tuple[str, ...]]] = []
        for surface, candidates in sorted(self._exact.items()):
            by_kind: dict[str, list[Sense]] = {}
            for sense in candidates:
                by_kind.setdefault(sense.theme_kind, []).append(sense)
            for senses in by_kind.values():
                terms = {sense.term for sense in senses}
                if len(terms) < 2:
                    continue
                declared = all(terms - {sense.term} <= sense.declared_ambiguity for sense in senses)
                if not declared:
                    found.append((surface, tuple(sorted(terms))))
        return tuple(found)

    def records(self, *, added_revision: int = 0) -> tuple[DictionaryEntry, ...]:
        """Return every sense as a persisted record, in canonical order."""
        return tuple(sense.as_record(added_revision=added_revision) for sense in self.senses())

    def fingerprint(self) -> str:
        """Return a digest of the whole vocabulary, in canonical order.

        Every conclusion can be made again from the Index, the Rulebook, the Dictionary and the
        Record. That is not true of a Dictionary that nobody wrote down. The digest fills
        ``RunRecord.dictionary_hash`` so that it is true.

        The digest is over the senses rather than the section files, because what a run actually
        consulted is the composed vocabulary. Two projects reaching the same senses by different
        section files consulted the same Dictionary, and a digest of the files would say otherwise.

        The properties are part of the digest. They are not decoration: a unit's dimension and
        factor, a doubt term's strength, the ambiguities a sense declares and whether it binds
        outside an extension all change what a run concludes. A digest that left them out would
        call two vocabularies the same while they answered differently. For example,
        :data:`EXTENSION_ONLY` can move thousands of bindings, and the hash that identifies the
        vocabulary must move with them. ``ontology_ids`` stay out deliberately: naming the same
        sense in an external ontology says nothing about binding.
        """
        payload: object = [
            {
                "term": sense.term,
                "canonical": sense.canonical,
                "theme_kind": sense.theme_kind,
                "surfaces": list(sense.surfaces),
                "scope": sense.scope.token,
                "origin": sense.origin.value,
                "status": sense.status.value,
                "properties": dict(sorted(sense.properties.items())),
            }
            for sense in self.senses()
        ]
        # A program's layout is vocabulary too: it changes which folders a rule reads as a program
        # and which files a dive reads last. The digest adds the layouts only where a section
        # declares one.
        layouts = program_layouts(self._sections)
        if layouts:
            payload = {
                "senses": payload,
                "layouts": [layout.as_payload() for layout in layouts],
            }
        # The word-forming endings are vocabulary too: they decide where a name divides.
        endings = sorted(self.word_endings())
        if endings:
            payload = {"vocabulary": payload, "word_endings": endings}
        # So are the reader's other lists: where a run never divides, which endings keep their s
        # and which kinds a plural never folds onto. The digest adds them only where a section
        # declares one, so a vocabulary holding none digests without them.
        lists = {
            name: sorted(listed)
            for name, listed in (
                ("word_prefixes", self.word_prefixes()),
                ("plural_keeps", self.plural_keeps()),
                ("plural_never_onto", self.plural_excluded_kinds()),
            )
            if listed
        }
        if lists:
            payload = {"vocabulary": payload, "reader_lists": lists}
        # The file-type table is always part of the digest. The format senses are made from it,
        # and what it says a kind of file is (recorded data or not, who writes it, what its bytes
        # begin with) is read by rules, so a changed table is a changed Dictionary.
        from ingence.dictionary.file_types import file_type_table

        payload = {"vocabulary": payload, "file_types": file_type_table().digest}
        return digest_text(canonical_json(payload))
