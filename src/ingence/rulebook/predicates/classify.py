"""Predicates that assign a classification to one entity.

A classifying rule answers "what is this", and not "what is this related to". It reads one
subject and returns a label or abstains. It never looks at a second entity, which keeps it out of
the pairwise machinery entirely.

Most of these predicates evaluate, because a classification is a function of one entry's own
records, and the census already produces those. The records are the components a name was cut
into, the shapes they sat in, and what the Dictionary made of each. Nothing about a classifying
rule waits on candidate generation. So the rules that read a name run, and the rules that read a
folder's rollup do not. The split falls where the records fall and not where it would be
convenient.

A predicate here takes one entry's records and never the project. A predicate that could reach
the project could write to it, and a narrow subject is what makes these testable from
constructed records without a run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json, dictionary_term_id, run_began_ns
from ingence.core.schema import (
    Component,
    DictionaryBinding,
    Entry,
    FailureClass,
    MatchQuality,
    Observation,
    RuleEffect,
    match_quality_meets,
)
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import INDEX_LABEL_CONSTRAINT
from ingence.dictionary.normalize import split_indexed
from ingence.rulebook.registry import (
    Abstention,
    Concluded,
    Predicate,
    PredicateNotEvaluableError,
    Reads,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

__all__ = [
    "ClassFromBoundSense",
    "ClassFromEnclosingFolder",
    "ClassFromFormatOrigin",
    "FormatFromExtension",
    "IndexOfBoundLabel",
    "NamingIsAuthoredOrEmitted",
    "PlaceholderFromEntryKind",
    "SenseFromNameSpelling",
    "SenseFromNameTokens",
    "StructureFromComponentKind",
    "credible_clocks",
    "earliest_credible_instant",
]


def _every_binding(subject: object) -> tuple[DictionaryBinding, ...]:
    """Every binding in a subject, whatever its status, which explaining an abstention needs.

    :func:`_bindings` yields only resolved bindings above a floor, which is right
    for reaching a verdict and wrong for saying why none was reached: the
    conflicting and unknown bindings are exactly the ones that explain it.
    """
    if not isinstance(subject, (list, tuple)):
        return ()
    return tuple(binding for binding in subject if isinstance(binding, DictionaryBinding))


def _on_axis(binding: DictionaryBinding, theme_kind: str) -> bool:
    """Whether a binding bears on an axis, by its settled kind or a kind it could not choose."""
    return binding.theme_kind == theme_kind or any(
        alternative.theme_kind == theme_kind for alternative in binding.alternatives
    )


def _stands_between(
    binding: DictionaryBinding, senses: frozenset[str], *, theme_kind: str | None = None
) -> bool:
    """Whether a binding could not choose between one of some named senses and another.

    A conflicting binding carries its candidates by identifier and not by name, so each
    named sense's identifier is recomputed under the scope and origin the candidate itself
    records: an exact comparison, not a guess at where the sense came from. A kind, where
    one is given, keeps the comparison to the candidates on that axis.
    """
    return any(
        alternative.term_id
        == dictionary_term_id(term=sense, scope=alternative.scope, origin=alternative.origin.value)
        for alternative in binding.alternatives
        if theme_kind is None or alternative.theme_kind == theme_kind
        for sense in senses
    )


def _identifiers(bindings: Iterable[DictionaryBinding]) -> tuple[str, ...]:
    """The identifiers of some bindings, in canonical order, for an abstention's inputs."""
    return tuple(sorted(binding.binding_id for binding in bindings))


def _bindings(
    subject: object, *, floor: MatchQuality = MatchQuality.LOOSE
) -> Iterator[DictionaryBinding]:
    """Yield the resolved bindings in a subject at or above a match quality.

    Abstains on a conflict by not yielding it. Where a binding is
    ``examined_conflicting`` the Dictionary found several senses standing equal
    and nothing about the token distinguishes them, so picking one would invent a
    fact: ``sd`` really is both a spinning disk and a standard deviation. The
    conflict stays in the table for anything that goes looking, which is what
    ``EXAMINED_CONFLICTING`` exists for.

    The quality floor reads a grade that the Dictionary records for each binding. A loose match is
    one reached after punctuation was stripped from both sides, which is a guess. On real laboratory
    folders, that guess turned the surface ``.part`` into the ordinary English word ``part`` and
    classified seventy-seven Parquet shards as interrupted downloads. The binding said ``loose`` on
    every one of them, and nothing was reading it. The evidence was recorded correctly and discarded
    at the only place that could have used it.

    Defaulting to ``LOOSE`` admits everything, which is the behavior of a rule that says nothing
    about quality. A rule that reads a surface whose specificity is its punctuation asks for
    ``EXACT``.
    """
    if not isinstance(subject, (list, tuple)):
        return
    for binding in subject:
        if not isinstance(binding, DictionaryBinding):
            continue
        if binding.resolution_status is not ResolutionStatus.RESOLVED:
            continue
        if not match_quality_meets(binding.quality, floor):
            continue
        yield binding


def _floor(parameters: Mapping[str, object]) -> MatchQuality:
    """Read the declared match-quality floor, admitting everything by default."""
    declared = parameters.get("minimum_match_quality")
    if declared is None:
        return MatchQuality.LOOSE
    try:
        return MatchQuality(str(declared))
    except ValueError:
        return MatchQuality.LOOSE


def _names(parameters: Mapping[str, object], key: str) -> frozenset[str]:
    """Read a list-valued parameter, tolerating the single-string spelling.

    A parameter carrying one name is the common case in a hand-edited file and
    reading it character by character is the failure a person would not think to
    look for.
    """
    value = parameters.get(key)
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item) for item in value)
    return frozenset()


def _named_senses(parameters: Mapping[str, object]) -> frozenset[str] | None:
    """The senses a rule names, or None where it names none and every sense on its axis counts.

    Left out is not an empty list. A rule writing an empty list names no sense and reads
    none, as a rule naming the senses of a class does, rather than reading them all.
    """
    if parameters.get("senses") is None:
        return None
    return _names(parameters, "senses")


#: Where a binding a rule reads was made: the extension slot, or the name's own tokens.
_EXTENSION_SLOT = "extension"
_NAME_SLOT = "name"
_SLOTS = (_EXTENSION_SLOT, _NAME_SLOT)


def _slot(parameters: Mapping[str, object]) -> str | None:
    """Return where a rule reads its bindings, or None where it reads both, refusing elsewhere."""
    declared = parameters.get("slot")
    if declared is None:
        return None
    if declared in _SLOTS:
        return str(declared)
    message = (
        f"class_from_bound_sense reads a binding from a file's extension or its name, and this "
        f"rule asks for {declared!r}"
    )
    raise PredicateNotEvaluableError(message)


def _slot_of(binding: DictionaryBinding) -> str:
    """Which slot of the name made a binding, read from the marker its lookup recorded.

    A marker is written ``<slot>:<restriction>``, so the slot is its first field. A binding that
    carries no marker was looked up unrestricted, which only a name token is.

    Presence of a marker is not the test. While the extension was the one restricted slot,
    "carries a marker" and "is the extension's" picked out the same bindings. A bare number also
    carries a marker, and reading presence would file every digit in a name under the extension.
    """
    if binding.constraint is None:
        return _NAME_SLOT
    return binding.constraint.split(":", 1)[0]


def _in_slot(binding: DictionaryBinding, slot: str | None) -> bool:
    """Whether a binding was made where the rule reads."""
    if slot is None:
        return True
    return _slot_of(binding) == slot


def _concluded_class(parameters: Mapping[str, object]) -> str | None:
    """The class a rule names as its conclusion, or None where it concludes the matched sense."""
    declared = parameters.get("concludes")
    if declared is None:
        return None
    if not isinstance(declared, str) or not declared.strip():
        message = (
            f"class_from_bound_sense concludes the class its rule names, and this rule names "
            f"{declared!r}, which is not a class"
        )
        raise PredicateNotEvaluableError(message)
    return declared.strip()


def _count(parameters: Mapping[str, object], key: str, default: int) -> int:
    """Read an integer parameter, falling back where a file wrote something else.

    A parameter's declared type is checked at load, so a value of the wrong type
    here means a caller passed an override the interface should have refused.
    Falling back rather than raising keeps a display concern from stopping a run,
    which is the same reasoning the renderer applies to a malformed default.
    """
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class FormatFromExtension:
    """A file's format is what its extension bound to, where that bound cleanly.

    The predicate is implemented and not merely declared, because everything it reads exists. The
    census splits the extension off before tokenising and binds it only to a sense whose theme kind
    is ``format``. So the binding that this predicate reads is already constrained to the right
    axis. The predicate adds the rule that a clean binding is a classification.

    It abstains on a conflict and does not choose. Where the binding is ``examined_conflicting``,
    the Dictionary found several senses standing equal, and nothing about the token distinguishes
    them. Picking one would invent a fact, and ``md`` really is both markdown and molecular
    dynamics.

    The subject is the entry's bindings. It is deliberately not an entry, a catalog or a project.
    A predicate that could reach the project could write to it, and the narrow subject is what
    makes this testable without a run.
    """

    name = "format_from_extension"
    #: Three: the verdict changed for unchanged input. Version 2 read a format word anywhere in a
    #: name. Version 3 reads the extension slot alone. Version 1 returned the term's identifier,
    #: and version 2 returned the term.
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the bound sense, or None where nothing settles it.

        The result is the sense's name and not its identifier. Version 1 returned the hash, on the
        argument that a term's spelling belongs to the sense and a renderer that wants the word asks
        the Dictionary. Running it on real laboratory folders showed what that costs: every verdict
        read as ``term_27cefd44dd…``, which is unreadable in any output and unusable as a parameter
        in any rule that wants to name the same sense. The binding carries both the name and the
        identifier.

        The rule reads the extension slot alone. A format word written inside a name binds the sense
        that an extension would, so a TIFF exported under the name of the ND2 it came from would be
        claimed an ND2 on the strength of a word. The rule's own sentence is "a file is of the
        format its extension names", and its rationale rests on the extension being what a
        filesystem carries. Neither is true of a word in the middle of a name.

        None is abstention and is a verdict. It means that the extension bound to nothing, bound
        to something that was not a format, or bound to several things equally. A later rule may
        still say what the file is from its content.
        """
        required_kind = str(parameters.get("theme_kind", "format"))
        for binding in _bindings(subject, floor=_floor(parameters)):
            if not _in_slot(binding, _EXTENSION_SLOT):
                continue
            if binding.theme_kind != required_kind:
                continue
            if binding.term is None:
                continue
            return binding.term
        return None

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why no format was concluded, which decides whose problem it is.

        A conflict is the rule unable to decide. Several senses on the axis stood equal, and
        nothing about the token separates them. This is the interpretation class. An extension that
        the Dictionary does not know starves the rule. The remedy is vocabulary and not a looser
        rule, which is what the comprehension class says. A match below the rule's floor is the
        rule declining on its own terms, and it is not a failure at all.

        Every branch reads the slot that the verdict reads. An abstention that blamed a word in the
        name for a rule that never consulted one would name the wrong token and send a person to
        fix the wrong thing.
        """
        required_kind = str(parameters.get("theme_kind", "format"))
        bindings = tuple(
            binding for binding in _every_binding(subject) if _in_slot(binding, _EXTENSION_SLOT)
        )
        conflicting = _identifiers(
            binding
            for binding in bindings
            if binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
            and _on_axis(binding, required_kind)
        )
        if conflicting:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=conflicting,
                reason=f"several senses of kind {required_kind} stood equal",
            )
        unknown = _identifiers(
            binding
            for binding in bindings
            if binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE
        )
        if unknown:
            return Abstention(
                failure_class=FailureClass.COMPREHENSION,
                inputs=unknown,
                reason="the Dictionary knows no format this extension spells",
            )
        floor = _floor(parameters)
        declined = _identifiers(
            binding
            for binding in bindings
            if binding.resolution_status is ResolutionStatus.RESOLVED
            and binding.theme_kind == required_kind
            and not match_quality_meets(binding.quality, floor)
        )
        if declined:
            return Abstention(inputs=declined, reason=f"matched below the {floor.value} floor")
        return Abstention(reason=f"nothing bound to a sense of kind {required_kind}")


class SenseFromNameTokens:
    """What a name's free tokens mean on one stated axis.

    The mechanism is the same as reading an extension, but the claim is much weaker. So the two
    are separate predicates. An extension occupies a constrained slot where a format's spelling is
    its extension, so the binding is nearly a fact. A word in the middle of a filename is a guess.
    It might be a technique, somebody's initials, or a word that happens to collide with one. The
    evidence is the same shape and is worth much less. So a rule that uses this predicate sits at a
    weaker tier than the extension rule, and asks for more than one hit before it says anything.

    Bindings that the extension slot produced are excluded. Counting a file's own extension as a
    name token counted one fact twice, on the most common token in the pool, and inflated every
    support figure computed over names. The binding records which lookups ran under a constraint,
    so the exclusion reads that and does not guess from a spelling.

    The predicate returns one hit per distinct sense and never one per spelling. Two tokens that
    reach the same sense are one fact written twice. Returning a set and not a count is how this
    predicate declines to make that mistake on a caller's behalf.

    A rule may name the senses it reads on its axis, with ``senses``. The stage axis holds pipeline
    stages and pixel transformations beside words that place a file in no pipeline, such as a
    folder of miscellany. A rule that concludes where a file sits names the stages it means. Left
    out, every sense on the axis counts, as in version 1. A named sense of another kind is never
    reached, because the axis is still the rule's.
    """

    name = "sense_from_name_tokens"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the distinct senses found on the stated axis, in canonical order."""
        required_kind = str(parameters.get("theme_kind", ""))
        named = _named_senses(parameters)
        minimum = max(1, _count(parameters, "minimum_senses", 1))
        found = {
            binding.term
            for binding in _bindings(subject, floor=_floor(parameters))
            if binding.constraint is None
            and binding.theme_kind == required_kind
            and binding.term is not None
            and (named is None or binding.term in named)
        }
        return tuple(sorted(found)) if len(found) >= minimum else ()

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why no sense on the axis was concluded.

        A name token that stands between senses on this axis is the rule unable to decide. Only a
        conflict on this axis counts: ``sd`` being a spinning disk or a standard deviation is a
        technique rule's problem and not a stage rule's. Fewer distinct senses than the rule asks
        for is the rule's own floor and no failure. A token that nobody has taught the Dictionary
        is not counted against a rule that reads one axis, because nothing says it belonged to that
        axis. A flag over unread vocabulary exists for that. Under a list of named senses, only a
        conflict with a named sense in it counts, since whichever of two unnamed senses a token
        meant, the rule would conclude nothing.
        """
        required_kind = str(parameters.get("theme_kind", ""))
        named = _named_senses(parameters)
        free = [binding for binding in _every_binding(subject) if binding.constraint is None]
        conflicting = _identifiers(
            binding
            for binding in free
            if binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
            and (
                _on_axis(binding, required_kind)
                if named is None
                else _stands_between(binding, named, theme_kind=required_kind)
            )
        )
        if conflicting:
            reason = (
                f"a name token stood between senses of kind {required_kind}"
                if named is None
                else (
                    f"a name token stood between one of the named senses of kind "
                    f"{required_kind} and another"
                )
            )
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=conflicting,
                reason=reason,
            )
        floor = _floor(parameters)
        found = [
            binding
            for binding in free
            if binding.resolution_status is ResolutionStatus.RESOLVED
            and binding.theme_kind == required_kind
            and binding.term is not None
            and match_quality_meets(binding.quality, floor)
            and (named is None or binding.term in named)
        ]
        if found:
            minimum = max(1, _count(parameters, "minimum_senses", 1))
            distinct = len({binding.term for binding in found})
            return Abstention(
                inputs=_identifiers(found),
                reason=f"{distinct} distinct senses of kind {required_kind}, {minimum} asked for",
            )
        if named is not None:
            return Abstention(
                reason=f"no name token bound to one of the named senses of kind {required_kind}"
            )
        return Abstention(reason=f"no name token bound to a sense of kind {required_kind}")


class ClassFromBoundSense:
    """A file is what a named set of senses says it is.

    The rule names the senses, and the Dictionary owns their spellings. This is the same separation
    that keeps domain vocabulary out of Python, applied one level up. A rule that says a vendor
    acquisition format is raw data names the sense. Which extensions spell that sense is the
    Dictionary's business, and a laboratory that adds a format to it does not touch the rule.

    The claim that the rule adds is one that the Dictionary does not make. The Dictionary knows
    that ``nd2`` names a proprietary acquisition format. It does not know that such a format is raw
    data and not an output, because that is an interpretation. Putting the interpretation in a rule
    makes it arguable, editable and attributable. Somebody who disagrees opens the sentence and
    removes a name from a list.

    The same shape serves filesystem noise and a folder whose name is organizational, at a
    different setting. Both are "these senses mean this class". Giving all three one mechanism
    means that a change to how sense matching works reaches all three and not two of them.

    A rule may name the class it concludes, with ``concludes``, where the class is not the sense
    that matched. An acquisition format means the stage ``acquisition``, which the proposing rule
    reads as a stage and to which no binding resolved. So the conclusion carries every binding that
    matched a named sense, because matching the concluded value against the records would find
    none. Left out, the rule concludes the sense that matched, as in version 1.

    A rule may say where the binding was made, with ``slot``. ``extension`` reads only what the
    extension slot bound, and ``name`` reads only the name's own tokens. Left out, both are read,
    as in version 2. A rule that reads a format needs ``extension``. A word such as ``nd2`` in the
    name of an exported TIFF binds the same format sense that an extension would. On a laboratory's
    tree, many acquisition stages and program classifications rested on such a word.
    """

    name = "class_from_bound_sense"
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the sense that matched, or the class the rule names, or None where none did."""
        wanted = _names(parameters, "senses")
        if not wanted:
            return None
        concludes = _concluded_class(parameters)
        slot = _slot(parameters)
        matched = [
            binding
            for binding in _bindings(subject, floor=_floor(parameters))
            if binding.term is not None and binding.term in wanted and _in_slot(binding, slot)
        ]
        if not matched:
            return None
        if concludes is None:
            return matched[0].term
        return Concluded(values=(concludes,), inputs=_identifiers(matched))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why none of the named senses was concluded.

        A token that stands between one of the named senses and another is the rule unable to
        decide. A conflicting binding carries its candidates by identifier and not by name. So each
        named sense's identifier is recomputed under the scope and origin that the candidate itself
        records. This is an exact comparison and not a guess at where the sense came from. A named
        sense that was reached only below the rule's floor is the rule declining on its own terms.
        It is not a failure. A floor of this kind keeps seventy-seven Parquet shards from being
        read as interrupted downloads.
        """
        wanted = _names(parameters, "senses")
        if not wanted:
            return Abstention(reason="the rule names no senses")
        slot = _slot(parameters)
        bindings = tuple(binding for binding in _every_binding(subject) if _in_slot(binding, slot))
        conflicting = _identifiers(
            binding
            for binding in bindings
            if binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
            and _stands_between(binding, wanted)
        )
        if conflicting:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=conflicting,
                reason="a token stood between one of the named senses and another",
            )
        floor = _floor(parameters)
        declined = _identifiers(
            binding
            for binding in bindings
            if binding.resolution_status is ResolutionStatus.RESOLVED
            and binding.term in wanted
            and not match_quality_meets(binding.quality, floor)
        )
        if declined:
            return Abstention(
                inputs=declined, reason=f"a named sense matched below the {floor.value} floor"
            )
        return Abstention(reason="nothing bound to one of the named senses")


#: Where in a name a spelling may sit, in the order a rule's positions are read.
_POSITIONS = ("whole", "prefix", "suffix")


class SenseFromNameSpelling:
    """What an entry's whole name, or the start or end of it, spells.

    This serves the vocabulary that sits where the census keeps no component. Operating systems,
    editors and sync clients mark what they make by a whole name, such as ``.DS_Store`` or
    ``Thumbs.db``, by a prefix, such as ``._``, or by a suffix, such as ``.part``. The census cuts
    a name into words, numbers and the like and binds those. It keeps neither a whole name nor an
    affix as a component, so the shipped noise rule, which reads bindings, could never fire on what
    it names. This predicate reads the entry's own name against every spelling of each sense that
    the rule names.

    The rule names the senses, the positions it reads, and any spelling it never reads. Which
    positions count is a judgment about names. A spelling that is left out is one that a person
    decided does not mean the sense in this pool: an office suite's lock file beside a table is
    somebody at work.

    The match is exact and case-sensitive, because each spelling is a symbol that a tool writes
    the same way every time. A whole name is the entry's name. A prefix or a suffix must leave
    something of the name beside it, so a file named only ``._`` is read as a whole name or not at
    all.
    """

    name = "sense_from_name_spelling"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the named senses the name spells at a position the rule reads."""
        wanted = _names(parameters, "senses")
        positions = _positions(parameters)
        excluded = _names(parameters, "excluded_surfaces")
        entry = getattr(subject, "entry", None)
        spellings = getattr(subject, "spellings", None) or {}
        if not wanted or not isinstance(entry, Entry):
            return ()
        return tuple(
            sorted(
                term
                for term in wanted
                if any(
                    _spelled_at(entry.name, surface, position)
                    for surface in spellings.get(term, ())
                    if surface not in excluded
                    for position in positions
                )
            )
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why no named sense was read: no spellings, or one the rule never reads."""
        wanted = _names(parameters, "senses")
        if not wanted:
            return Abstention(reason="the rule names no senses")
        entry = getattr(subject, "entry", None)
        spellings = getattr(subject, "spellings", None) or {}
        if not isinstance(entry, Entry) or not spellings:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason="no spellings were handed over, so the name was read against none",
            )
        excluded = _names(parameters, "excluded_surfaces")
        passed_over = sorted(
            {
                surface
                for term in wanted
                for surface in spellings.get(term, ())
                if surface in excluded
                and any(_spelled_at(entry.name, surface, position) for position in _POSITIONS)
            }
        )
        if passed_over:
            return Abstention(
                inputs=(entry.entry_id,),
                reason=(
                    f"the name is spelled with {', '.join(passed_over)}, which the rule never reads"
                ),
            )
        return Abstention(reason="the name spells none of the named senses where the rule reads")


def _positions(parameters: Mapping[str, object]) -> tuple[str, ...]:
    """Return the positions a rule reads a spelling at, refusing none or one no name has."""
    declared = _names(parameters, "positions")
    unknown = sorted(declared - frozenset(_POSITIONS))
    if unknown:
        message = (
            f"sense_from_name_spelling reads a spelling as a whole name, a prefix or a suffix, "
            f"and this rule asks for {', '.join(unknown)}"
        )
        raise PredicateNotEvaluableError(message)
    if not declared:
        message = (
            "sense_from_name_spelling reads a spelling where a rule's positions say, and this "
            "rule names no position, so it could never match"
        )
        raise PredicateNotEvaluableError(message)
    return tuple(position for position in _POSITIONS if position in declared)


def _spelled_at(name: str, surface: str, position: str) -> bool:
    """Whether a name is a spelling, or begins or ends with one and holds more beside it."""
    if not surface:
        return False
    if position == "whole":
        return name == surface
    if len(name) <= len(surface):
        return False
    return name.startswith(surface) if position == "prefix" else name.endswith(surface)


class StructureFromComponentKind:
    """What a name's structure says, before anything is looked up.

    Tokenising a name produces components of declared kinds: a word, a date, a bare number, a
    version marker, an indexed label, a quantity with its unit. The grammar of the string
    establishes the kind, and the Dictionary does not. So a rule that reads a kind stands on much
    firmer footing than a rule that reads a sense. ``rep3`` is an indexed label in any laboratory
    in any field, and ``500nM`` is a concentration whether or not anybody has heard of what was at
    that concentration.

    This is the mechanism behind four separate rules, and they differ only in which kind they ask
    for. A version marker says the file is one version of something. An indexed label says it is
    a member of a series. A quantity says a condition was recorded in the name. A date says when
    somebody thought it happened. These are four claims with one reader. The difference between
    them lives in the rule file, where a person can see it.

    Quantities are compared on the normalized magnitude and not the spelling. ``40min`` and
    ``2400s`` are one condition written two ways, and the normalizer already reduced both to a
    dimension and a base magnitude. A rule that compared spellings would find two conditions where
    there is one. The normalizer exists to prevent that.
    """

    name = "structure_from_component_kind"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the normalized values of the components of the stated kind."""
        wanted = _names(parameters, "component_kinds")
        if not wanted or not isinstance(subject, (list, tuple)):
            return ()
        found = {
            component.normalized_value
            for component in subject
            if isinstance(component, Component) and component.component_type in wanted
        }
        return tuple(sorted(found))


class ClassFromFormatOrigin:
    """A file is what a named set of senses says it is, where its spelling's writer is named.

    The type says what a file holds, and the origin says who can have written it. The proprietary
    file types limit their origins. One image type holds TIFF, PNG and ND2 alike, because all three
    hold images. So a rule that says an instrument's own format is raw data cannot read the type.
    It reads the origin that the Dictionary declares for the spelling that bound. ``nd2`` is
    written by an instrument as it acquires. ``tif`` declares no origin, because anything writes
    one. ``ims`` is written by software.

    A sense that declares no origins is taken whole, as ``class_from_bound_sense`` takes every
    sense. An electrophysiology format is an instrument's throughout, and a laboratory that
    declares origins on it narrows the rule without touching it. A sense that declares origins
    admits only a spelling whose writer the rule names. A spelling that it does not declare is not
    admitted, because an undeclared spelling of a declared sense is a file that anything could have
    written.

    In version 2, a sense that declares an empty map declares origins and admits no spelling. The
    core's image type is that sense where no microscopy pack is loaded. It holds TIFF, PNG and
    JPEG, which anything writes, and the spellings an instrument writes arrive with the pack.
    Version 1 read an empty map as no declaration, and it would have made every TIFF an
    instrument's the moment a project named no domain.

    Everything else is ``class_from_bound_sense``'s, applied to the bindings that the origin
    admits. That covers the senses, the floor, the slot and the class concluded.
    """

    name = "class_from_format_origin"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return what ``class_from_bound_sense`` returns over the bindings the origin admits."""
        return ClassFromBoundSense().evaluate(self._admitted(subject, parameters), parameters)

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why, naming a matched sense whose spelling's writer the rule does not name."""
        admitted = self._admitted(subject, parameters)
        everything = list(_every_binding_of(getattr(subject, "bindings", ())))
        wanted = _names(parameters, "senses")
        slot = _slot(parameters)
        refused = [
            binding
            for binding in _bindings(everything, floor=_floor(parameters))
            if binding.term in wanted and _in_slot(binding, slot) and binding not in admitted
        ]
        if refused:
            writers = ", ".join(sorted(_names(parameters, "written_by"))) or "no writer"
            return Abstention(
                inputs=_identifiers(refused),
                reason=(
                    f"the spelling bound to a named sense is not declared as written by {writers}"
                ),
            )
        return ClassFromBoundSense().explain_abstention(admitted, parameters)

    @staticmethod
    def _admitted(subject: object, parameters: Mapping[str, object]) -> list[DictionaryBinding]:
        """The subject's bindings, less those of a declared sense whose spelling's writer is
        not one the rule names."""
        writers = _names(parameters, "written_by")
        if not writers:
            message = (
                "class_from_format_origin reads who writes a spelling, and this rule names no "
                "writer; with none it would admit nothing a sense declares an origin for"
            )
            raise PredicateNotEvaluableError(message)
        origins = getattr(subject, "origins", None) or {}
        spelled = {
            component.component_id: component.normalized_value.casefold()
            for component in _every_component(getattr(subject, "components", ()))
        }
        admitted: list[DictionaryBinding] = []
        for binding in _every_binding_of(getattr(subject, "bindings", ())):
            declared = origins.get(binding.term) if binding.term is not None else None
            if declared is None:
                admitted.append(binding)
                continue
            if declared.get(spelled.get(binding.component_id, "")) in writers:
                admitted.append(binding)
        return admitted


class PlaceholderFromEntryKind:
    """An entry standing in for content that is not here.

    A symbolic link, and on tiered or archival storage a stub left behind when a file's body was
    moved to slower media. The filesystem will report a size and a name for both, and neither holds
    the bytes. An engine that reads them as ordinary files records a content failure where the
    honest answer is that the content is elsewhere and could be fetched.

    A placeholder is not a comprehension failure, because nothing is wrong with the parser. It is
    not an access failure, because nothing is wrong with the permissions. It is a fact about the
    file, and classifying it as one stops it being counted against either.

    The stub half is a heuristic and the link half is not. A symbolic link is what the filesystem
    says it is. A zero-length file with a substantial declared size is a stub on some systems and
    an ordinary empty file on all of them. So the rule that uses this predicate carries the
    threshold, and the tier says what it is worth.
    """

    name = "placeholder_from_entry_kind"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the kind of placeholder, or None where the entry holds its own bytes."""
        if not isinstance(subject, Entry):
            return None
        if subject.entry_type == "symlink":
            return "symbolic_link"
        if not bool(parameters.get("detect_stubs", True)):
            return None
        declared = subject.size_bytes
        allocated = subject.allocated_bytes
        floor = max(1, _count(parameters, "minimum_declared_bytes", 1))
        if declared is None or allocated is None:
            return None
        if declared >= floor and allocated == 0:
            return "offline_stub"
        return None

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the entry holds its own bytes, or whether nothing could say.

        A filesystem that reports no allocation figure cannot tell a stub from a file, so the stub
        half never looked. That is unexamined and not no evidence. Not every filesystem reports
        one, and treating its silence as a finding would say the entry holds its bytes when
        nothing established it.
        """
        if not isinstance(subject, Entry):
            return Abstention(reason="handed something other than an entry")
        missing = subject.size_bytes is None or subject.allocated_bytes is None
        if bool(parameters.get("detect_stubs", True)) and missing:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason="no allocation figure was reported, so a stub cannot be told from a file",
            )
        return Abstention(reason="the entry holds its own bytes")


class ClassFromEnclosingFolder:
    """A file inherits what its folder states and it does not restate.

    This is "understood by context", made mechanical. A file in a folder whose name states a date,
    a condition or a stage belongs to that date, condition or stage without repeating it. This is
    how people actually name things: the distinguishing part goes in the filename and the shared
    part goes in the folder above it.

    The walk is bounded at one hop, hard, and this is not a parameter. ``contains`` is transitive,
    but the inference is not. If A holds B which holds C, an attribute of A that reaches C means
    every child of A now shares it, including branches nobody was looking at. A decaying weight
    invites somebody to set the decay low and reach three levels up. A hard bound does not.

    What one hop means is supplied by the regime, which is why the rule is defeasible. In a
    workflow bundle, the enclosing folder is the experiment. In a type silo it is the assay, shared
    across every experiment. In a date bucket it is a time slice and little else. The distance is
    the same, but there are three propositions. A rule that reads the first where the third holds
    will inherit confidently and wrongly. The characteriser records the features that would say
    which regime applies. Nothing validates the assignment yet, so the rule stays weak on purpose.

    Declared and not evaluable. It needs the enclosing folder's own bindings beside the file's,
    which is a rollup that nothing assembles yet.
    """

    name = "class_from_enclosing_folder"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "class_from_enclosing_folder reads the enclosing folder's bindings beside "
            "the file's own, and nothing assembles the two together yet"
        )
        raise PredicateNotEvaluableError(message)


#: The component type an indexed atom writes. One spelling across the modules that read the
#: same components: the grouping filter, the scope flag and this.
_INDEXED_COMPONENT = "indexed"


def _every_binding_of(subject: object) -> tuple[DictionaryBinding, ...]:
    """The bindings in a subject, from a sequence of them or from an entry's records."""
    if isinstance(subject, (list, tuple)):
        return tuple(item for item in subject if isinstance(item, DictionaryBinding))
    return tuple(
        item for item in getattr(subject, "bindings", ()) if isinstance(item, DictionaryBinding)
    )


def _every_component(subject: object) -> tuple[Component, ...]:
    """Every component in a subject, from a sequence of them or from an entry's records."""
    if isinstance(subject, (list, tuple)):
        return tuple(item for item in subject if isinstance(item, Component))
    return tuple(item for item in getattr(subject, "components", ()) if isinstance(item, Component))


def _cited(found: Mapping[str, Mapping[int, str]]) -> tuple[str, ...]:
    """The bindings behind a set of numbered senses, in canonical order."""
    return tuple(sorted({binding for indices in found.values() for binding in indices.values()}))


def _indexed_senses(subject: object, parameters: Mapping[str, object]) -> dict[str, dict[int, str]]:
    """Return, per sense a label bound, each index found for it and the binding that said so.

    A sense numbered once is a conclusion; a sense numbered twice in one name is a conflict,
    and returning both lets the verdict and the abstention read the same map rather than
    computing the same comparison in two places.
    """
    required_kind = str(parameters.get("theme_kind", ""))
    if not required_kind:
        return {}
    named = _named_senses(parameters)
    floor = _floor(parameters)
    values = {
        component.component_id: component.normalized_value
        for component in _every_component(subject)
        if component.component_type == _INDEXED_COMPONENT
    }
    # This predicate reads an entry's whole records, because the sense is on a binding and
    # the number beside it is on the component. `_bindings` reads a sequence, which is what
    # every other predicate here is handed, so the bindings are lifted out and passed to it
    # rather than the filter being written a second time.
    found: dict[str, dict[int, str]] = defaultdict(dict)
    for binding in _bindings(_every_binding_of(subject), floor=floor):
        if binding.constraint != INDEX_LABEL_CONSTRAINT:
            continue
        if binding.theme_kind != required_kind or binding.term is None:
            continue
        if named is not None and binding.term not in named:
            continue
        value = values.get(binding.component_id)
        split = None if value is None else split_indexed(value)
        if split is None:
            continue
        found[binding.term][split[1]] = binding.binding_id
    return dict(found)


class IndexOfBoundLabel:
    """Which repetition a name says a file is, where its label bound a sense on the axis.

    The conclusion is the sense and the index together, because neither alone is the fact. ``br2``
    says biological replicate two. The sense without the index says only that a repetition
    happened. The index without the sense is a position with no meaning attached, and two files
    numbered 2 on different axes are not the same thing. So the conclusion is the pair, written
    canonically, and one claim carries both.

    The count is per sense and never pooled into one number. Three technical replicates of one
    dish is n = 1, and a tool that reports "3 replicates" without saying which kind invites the
    commonest statistical error in cell biology. The bare ``replicate`` sense is an unspecified
    kind and not a third kind. Nothing here infers one kind from another.

    Two numbers for one sense is a conflict and not a choice. A name that carries both ``br1`` and
    ``br2`` does not say which replicate the file is, and picking the first would make the answer
    depend on component order. It is an interpretation failure, which is what
    :class:`~ingence.core.schema.RuleFailure` exists to record. Two numbers on different senses
    are two facts, and both are concluded.

    The predicate reads only what the label bound. The binder tries an indexed component's whole
    text first, and its label only where that reached nothing. A label binding records the marker
    ``name:replicate``. The predicate reads the marker and not the slot, because a bare number
    carries a name-slot marker too, and testing the slot would read every digit in a name as a
    repetition.

    The index comes from the component and never from the binding. A binding says what the label
    means. The number beside it is in the component's own normalized value, which
    :func:`~ingence.dictionary.normalize.split_indexed` reads back beside the pattern that wrote
    it.
    """

    name = "index_of_bound_label"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return one value per sense the labels bound, or refuse where one carries two numbers."""
        found = _indexed_senses(subject, parameters)
        if not found:
            return None
        divided = sorted(sense for sense, indices in found.items() if len(indices) > 1)
        if divided:
            return None
        return Concluded(
            values=tuple(
                canonical_json({"index": next(iter(indices)), "sense": sense})
                for sense, indices in sorted(found.items())
            ),
            inputs=_cited(found),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether a sense was numbered twice, or no label bound one at all."""
        found = _indexed_senses(subject, parameters)
        divided = {sense: indices for sense, indices in found.items() if len(indices) > 1}
        if divided:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=_cited(divided),
                reason=(
                    f"{', '.join(sorted(divided))} is numbered "
                    f"{', '.join(str(len(indices)) for _, indices in sorted(divided.items()))} "
                    f"ways in one name, and nothing says which"
                ),
            )
        kind = str(parameters.get("theme_kind", ""))
        return Abstention(reason=f"no indexed label in the name binds a sense of kind {kind}")


class NamingIsAuthoredOrEmitted:
    """Whether a person typed this name or a machine generated it.

    This is a cheap reading with unusual leverage. Almost every other signal in the engine is about
    what a file contains. This one is about whether anybody made a decision when naming it. That
    changes what every other signal is worth. A token in a name that somebody chose is evidence
    about intent, and the same token in a name that a camera emitted is evidence about firmware.

    The negative lexicon doubles as an authorship detector at no cost. Nothing machine-generated
    emits ``final_final`` or ``use_this_one``, so a hit in a name is by construction evidence that
    a human typed it. The other direction reads structure and not vocabulary. A long fixed prefix
    with a zero-padded counter, repeated across a folder's siblings, is an emitter's signature.

    Neither reading is the absence of the other, and the null is stated. A name that is neither
    authored nor emitted is not half-authored. It carries no signal either way, and reporting weak
    authorship there would put a number on an absence.

    Declared and not evaluable. The emitted half compares a name against its siblings, which the
    folder characteriser computes. That is a folder-level observation and not an entry's own
    record.
    """

    name = "naming_is_authored_or_emitted"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "naming_is_authored_or_emitted compares a name against its siblings, which "
            "is a folder-level observation rather than an entry's own record"
        )
        raise PredicateNotEvaluableError(message)


@declare(
    "format_from_extension",
    version="3",
    summary="Classify a file by the format sense its extension bound to",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.BINDINGS,
    parameters=("minimum_match_quality", "theme_kind"),
    support_families=("format_binding",),
)
def _build_format_from_extension() -> Predicate:
    return FormatFromExtension()


@declare(
    "index_of_bound_label",
    version="1",
    summary="Conclude which repetition a name says a file is, from a label and its index",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.RECORDS,
    parameters=("minimum_match_quality", "senses", "theme_kind"),
    support_families=("name_binding",),
)
def _build_index_of_bound_label() -> Predicate:
    return IndexOfBoundLabel()


@declare(
    "sense_from_name_tokens",
    version="2",
    summary="Classify a file by what its free name tokens mean on one axis",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.BINDINGS,
    parameters=("minimum_match_quality", "minimum_senses", "senses", "theme_kind"),
    support_families=("name_binding",),
)
def _build_sense_from_name_tokens() -> Predicate:
    return SenseFromNameTokens()


@declare(
    "class_from_bound_sense",
    version="3",
    summary="Classify an entry by whether it bound to one of a named set of senses",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.BINDINGS,
    parameters=("concludes", "minimum_match_quality", "senses", "slot"),
    support_families=("name_binding",),
)
def _build_class_from_bound_sense() -> Predicate:
    return ClassFromBoundSense()


@declare(
    "class_from_format_origin",
    version="2",
    summary="Classify an entry by a named sense whose spelling a named kind of writer writes",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.FORMAT_ORIGIN,
    parameters=("concludes", "minimum_match_quality", "senses", "slot", "written_by"),
    support_families=("name_binding",),
)
def _build_class_from_format_origin() -> Predicate:
    return ClassFromFormatOrigin()


@declare(
    "sense_from_name_spelling",
    version="1",
    summary="Classify an entry by a sense its whole name, or the start or end of it, spells",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.NAME_SPELLINGS,
    parameters=("excluded_surfaces", "positions", "senses"),
    support_families=("name_spelling",),
)
def _build_sense_from_name_spelling() -> Predicate:
    return SenseFromNameSpelling()


@declare(
    "structure_from_component_kind",
    version="1",
    summary="Classify a file by the structural kinds its name was cut into",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.COMPONENTS,
    parameters=("component_kinds",),
    support_families=("name_structure",),
)
def _build_structure_from_component_kind() -> Predicate:
    return StructureFromComponentKind()


class EarliestCredibleTime:
    """When a file first existed, as far as its own records can say, and which record says so.

    The rule prefers a file's birth over what is evidently a copy's time. It is a general,
    interpretable rule that defaults to a creation time or the earliest time a file is recorded
    having existed, according to its own data or contents. Many files are born after
    they were last modified, because a copy keeps a file's modification time and is born when it
    lands. So the rule reads these sources in order, and each value names its source:

    - ``instrument_clock`` or ``document_clock``: the time the file's own content states, where
      a dive read it;
    - ``birth``: the filesystem's creation time, where it is not later than the modification;
    - ``modified_before_born``: the modification time, where the file was born after it. This is a
      copy, whose birth is when it arrived and whose modification is when its content was made;
    - ``modification``: the modification time, where the filesystem records no birth.

    The value is canonical JSON, ``{"at": <ISO 8601, UTC, to the second>, "source": <which>}``,
    so a claim says both when and why, and two runs over an unchanged pool agree to the byte.
    Nothing is read that the census and a dive did not already record.

    Version 2 concludes a content clock where a dive read one. Otherwise it concludes the birth,
    where the birth is not later than the modification. Where the birth comes after the
    modification, as after a copy, it concludes the modification time. Version 1 did the same.
    Version 2 also treats impossible times as
    not credible. File creation dates can be absurd, such as a file that says it is from 1969. A
    time before ``earliest_credible_year``, or after the run that observed the file (read from
    that run's identifier) by more than ``future_tolerance_seconds``, is set aside and named in
    the value (``"set_aside"``). The next source is read instead, and a file with no credible
    time concludes nothing and says why.

    Version 3 does not take a template's creation stamp as the time the file first existed. A
    document made from a template (a blank workbook, presentation or document) can carry the
    template's own creation stamp, written at midnight UTC years before anyone saved a document
    from it, while the same package's modification stamp is the real save. So a document clock
    that is a package's creation stamp at exactly midnight UTC and at least
    ``template_stamp_minimum_gap_days`` before the same package's own modification stamp is not
    believed. The value names it under ``"not_believed"`` with the reason, and the filesystem's
    times are read instead. The stamp itself stays what the package says: the dive's observation
    and the file's content clock are unchanged. Only this conclusion changes. A creation stamp
    with a time of day is believed however old, because a document begun years ago and still
    being edited is a long-lived document and not a template's. Only an Office package's stamp,
    written to the second, is judged. The readers named in ``template_stamp_observers`` count,
    a PDF's dates never do, and a stamp that states a day alone never does. Where the stamp is not
    believed, the package's own last save joins the filesystem's times, and the earliest is
    concluded. It is named ``document_last_saved`` where it is earlier than the disk's earliest
    time by more than ``future_tolerance_seconds``, the skew allowed between machines' clocks. So a
    file copied years after its last save keeps that save, and a save stamped a moment before the
    copy's birth, or hours before it on a skewed clock, does not displace it. There is one known
    limit: an untouched copy of a blank template holds the template's stamp as both creation and
    last save, with a gap of zero, and is believed. Version 3 reads the entry's records whole,
    which are its observations beside its filesystem facts. An entry handed alone is read as
    version 2 read it.
    """

    name = "earliest_credible_time"
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the earliest credible time and its source, or None where no clock exists."""
        entry = _entry_of(subject)
        if entry is None or entry.entry_type == "directory":
            return None
        template = _template_stamp(entry, _observations_of(subject), parameters)
        found, set_aside = _earliest(entry, _credible(entry, parameters), template)
        if found is None:
            return None
        at_ns, source = found
        said: dict[str, object] = {"at": _instant_of(at_ns), "source": source}
        if set_aside:
            said["set_aside"] = sorted(set_aside)
        cited: tuple[str, ...] = ()
        if template is not None:
            said["not_believed"] = [template.said]
            cited = template.cited
        return Concluded(values=(canonical_json(said),), inputs=(entry.entry_id, *cited))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no clock was recorded for the entry, or that it is a folder."""
        entry = _entry_of(subject)
        if entry is not None and entry.entry_type == "directory":
            return Abstention(reason="a folder has an interval, not an instant it first existed")
        if entry is not None:
            template = _template_stamp(entry, _observations_of(subject), parameters)
            _, set_aside = _earliest(entry, _credible(entry, parameters), template)
            if template is not None:
                return Abstention(
                    inputs=(entry.entry_id, *template.cited),
                    reason=template.said + "; and no other credible time was recorded for it",
                )
            if set_aside:
                return Abstention(
                    reason="every time recorded for it is impossible: "
                    + ", ".join(sorted(set_aside))
                )
        return Abstention(
            status=ResolutionStatus.UNEXAMINED,
            reason="neither its content nor the filesystem recorded a time for it",
        )


def _entry_of(subject: object) -> Entry | None:
    """The entry a subject is: an entry handed alone, or the entry its records are about."""
    if isinstance(subject, Entry):
        return subject
    held = getattr(subject, "entry", None)
    return held if isinstance(held, Entry) else None


def _observations_of(subject: object) -> tuple[Observation, ...]:
    """The observations handed with an entry's records, in their canonical order; none for an
    entry handed alone."""
    held = getattr(subject, "observations", ())
    return tuple(
        observation
        for observation in (held if isinstance(held, (list, tuple)) else ())
        if isinstance(observation, Observation)
    )


#: Nanoseconds in a day: a stamp at midnight UTC is a whole number of them since the epoch.
_DAY_NS = 86_400 * 1_000_000_000

#: The declared default of ``template_stamp_minimum_gap_days``. Microsoft's blank workbook and
#: presentation packages carry creation stamps of 2006 (16 September and 16 August, at midnight
#: UTC), seventeen years and more before any document saved from them since 2023. A year is far
#: inside that gap and still keeps a document genuinely begun at midnight and saved within the
#: year. The gap is measured on the template stamps themselves, never on a laboratory's files.
TEMPLATE_STAMP_MINIMUM_GAP_DAYS = 365

#: The declared default of ``template_stamp_observers``: the extractors that read an Office
#: package's core properties (``extractors/ooxml.py``, shared by the document and the workbook
#: readers). Only a package made from a template carries a template's stamp. A PDF's dates, which
#: the PDF extractor records under the same keys, are never judged.
TEMPLATE_STAMP_OBSERVERS = ("office", "spreadsheet")

#: The resolutions a template's stamp is written at: a time of day, to the second or finer. A
#: stamp stating a day alone is a date somebody or something wrote without a time, never the
#: blank template's signature (``2006-09-16T00:00:00Z``), so it is believed.
_TEMPLATE_RESOLUTIONS = frozenset({"second", "subsecond"})

#: The observation keys a package's own creation and modification stamps are recorded under, by
#: their endings (``document.created_ns``, ``table.created_ns``), with the resolution recorded
#: beside each (``document.created_resolution``): the shared OOXML reading's vocabulary.
_CREATED_KEY = ".created_ns"
_MODIFIED_KEY = ".modified_ns"
_CREATED_RESOLUTION_KEY = ".created_resolution"


@dataclass(frozen=True, slots=True)
class _Template:
    """A document clock judged a template's creation stamp: why, in plain words, the package's
    own last save, and the observations that say so."""

    said: str
    saved_ns: int
    cited: tuple[str, ...]
    #: How much earlier than the disk's earliest time the last save must be to be taken: the
    #: rule's ``future_tolerance_seconds``, the skew allowed between machines' clocks.
    margin_ns: int = 0


def _template_stamp(
    entry: Entry, observations: tuple[Observation, ...], parameters: Mapping[str, object]
) -> _Template | None:
    """The entry's document clock judged a template's creation stamp, or None where it is not,
    or where nothing handed can say.

    Cheap refusals first (the abstention path is the hot one): only a document clock at exactly
    midnight UTC is looked at, so most files never reach their observations. Then only a stamp an
    Office package's core properties state (``template_stamp_observers``), written to the second
    or finer, whose package's own last save lies ``template_stamp_minimum_gap_days`` or more
    after it.
    """
    if entry.event_time_ns is None or str(entry.event_time_source) != "document":
        return None
    created_ns = int(entry.event_time_ns)
    if created_ns % _DAY_NS != 0 or not observations:
        return None
    gap_days = int(
        str(parameters.get("template_stamp_minimum_gap_days", TEMPLATE_STAMP_MINIMUM_GAP_DAYS))
    )
    observers = (
        _names(parameters, "template_stamp_observers")
        if ("template_stamp_observers" in parameters)
        else frozenset(TEMPLATE_STAMP_OBSERVERS)
    )
    stated = [
        observation
        for observation in observations
        if observation.key.endswith(_CREATED_KEY)
        and observation.value_integer == created_ns
        and observation.observer in observers
    ]
    for created in sorted(stated, key=lambda observation: observation.observation_id):
        namespace = created.key[: -len(_CREATED_KEY)]
        resolutions = {
            str(observation.value_text)
            for observation in observations
            if observation.key == namespace + _CREATED_RESOLUTION_KEY
            and observation.observer == created.observer
        }
        if not resolutions or not resolutions <= _TEMPLATE_RESOLUTIONS:
            continue
        saves = sorted(
            (
                (int(observation.value_integer), observation.observation_id)
                for observation in observations
                if observation.key == namespace + _MODIFIED_KEY
                and observation.observer == created.observer
                and observation.value_integer is not None
            ),
            reverse=True,
        )
        if not saves:
            continue
        saved_ns, saved_id = saves[0]
        if saved_ns - created_ns < gap_days * _DAY_NS:
            continue
        said = (
            f"the date written inside it, {_day_in_words(created_ns)} at midnight, looks like the "
            f"date of the blank template it was made from, {(saved_ns - created_ns) // _DAY_NS:,} "
            f"days before it was last saved ({_day_in_words(saved_ns)}), so it is not used "
            f"(the setting for the least gap between the two dates is {gap_days:,} days)"
        )
        margin = int(str(parameters.get("future_tolerance_seconds", FUTURE_TOLERANCE_SECONDS)))
        return _Template(
            said=said,
            saved_ns=saved_ns,
            cited=(created.observation_id, saved_id),
            margin_ns=margin * 1_000_000_000,
        )
    return None


def _day_in_words(nanoseconds: int) -> str:
    """A day as a person writes it: ``16 September 2006``."""
    import datetime as dt

    moment = dt.datetime.fromtimestamp(nanoseconds // 1_000_000_000, tz=dt.UTC)
    return f"{moment.day} {moment:%B %Y}"


#: The content clocks a dive reads, which outrank every filesystem time.
_CONTENT_CLOCKS = {"instrument": "instrument_clock", "document": "document_clock"}


def _credible(entry: Entry, parameters: Mapping[str, object]) -> Callable[[int], bool]:
    """Whether a time is possible for this file: not before the earliest credible year, and not
    after the run that observed it by more than the tolerance."""
    import datetime as dt

    year = int(str(parameters.get("earliest_credible_year", EARLIEST_CREDIBLE_YEAR)))
    floor = int(dt.datetime(year, 1, 1, tzinfo=dt.UTC).timestamp()) * 1_000_000_000
    observed = run_began_ns(entry.last_seen_run or "")
    tolerance = int(str(parameters.get("future_tolerance_seconds", FUTURE_TOLERANCE_SECONDS)))
    ceiling = None if observed is None else observed + tolerance * 1_000_000_000

    def possible(nanoseconds: int) -> bool:
        return nanoseconds >= floor and (ceiling is None or nanoseconds <= ceiling)

    return possible


#: The declared defaults of ``earliest_credible_time``'s two bounds, measured on laboratory
#: folders: no file or content time within days of the epoch, and none after its census. The year
#: sits well below every time measured, for laboratories older than these, and above the epoch's
#: 1969-1970 artifacts. A day's tolerance covers clock skew.
EARLIEST_CREDIBLE_YEAR = 1990
FUTURE_TOLERANCE_SECONDS = 86_400


def _earliest(
    entry: Entry,
    possible: Callable[[int], bool] = lambda _: True,
    template: _Template | None = None,
) -> tuple[tuple[int, str] | None, list[str]]:
    """The earliest credible instant, in nanoseconds, the source that says so, and what was set
    aside as impossible. A content clock judged a template's is passed over, and the package's own
    last save joins the filesystem's times (``document_last_saved`` only where earlier than the
    filesystem's earliest by more than the template's margin)."""
    set_aside: list[str] = []

    def kept(nanoseconds: int | None, source: str) -> int | None:
        if nanoseconds is None:
            return None
        if possible(int(nanoseconds)):
            return int(nanoseconds)
        set_aside.append(f"{source} {_instant_of(int(nanoseconds))}")
        return None

    if (
        template is None
        and entry.event_time_ns is not None
        and str(entry.event_time_source) in _CONTENT_CLOCKS
    ):
        source = _CONTENT_CLOCKS[str(entry.event_time_source)]
        content = kept(entry.event_time_ns, source)
        if content is not None:
            return (content, source), set_aside
    birth = kept(
        entry.created_ns if str(entry.created_time_source) == "birthtime" else None, "birth"
    )
    modified = kept(entry.modified_ns, "modification")
    filesystem: tuple[int, str] | None = None
    if birth is not None and modified is not None:
        filesystem = (modified, "modified_before_born") if birth > modified else (birth, "birth")
    elif modified is not None:
        filesystem = (modified, "modification")
    elif birth is not None:
        filesystem = (birth, "birth")
    saved = kept(template.saved_ns, "document_last_saved") if template is not None else None
    # Taken only where earlier than the disk's earliest time by more than the clocks' skew. A save
    # by writing a new file and renaming it stamps a second cut before the birth, and a machine
    # hours behind stamps a save made after the copy before it.
    margin = template.margin_ns if template is not None else 0
    if saved is not None and (filesystem is None or filesystem[0] - saved > margin):
        return (saved, "document_last_saved"), set_aside
    return filesystem, set_aside


def earliest_credible_instant(entry: Entry) -> tuple[int, str] | None:
    """An entry's earliest credible instant in nanoseconds and its source, or None.

    What ``earliest_credible_time`` concludes, at its declared bounds, read from the entry alone:
    the order a derivation takes where its evidence gives none (the proposing rules' time order).
    """
    found, _ = _earliest(entry, _credible(entry, {}))
    return found


def credible_clocks(entry: Entry) -> tuple[tuple[int, str] | None, tuple[int, str] | None]:
    """An entry's content clock and its filesystem clock, each with its source, or None.

    The two kinds ``earliest_credible_time`` chooses between, read apart: a content clock travels
    with the bytes, a filesystem clock describes this copy on this disk, so two ends are ordered
    only by clocks of one kind. The filesystem clock is the earliest of birth and modification,
    named ``modified_before_born`` where the file was born after it was changed (a copy).
    """
    possible = _credible(entry, {})
    content: tuple[int, str] | None = None
    if (
        entry.event_time_ns is not None
        and str(entry.event_time_source) in _CONTENT_CLOCKS
        and possible(int(entry.event_time_ns))
    ):
        content = (int(entry.event_time_ns), _CONTENT_CLOCKS[str(entry.event_time_source)])

    # Read from the entry's own fields, which a record and the pass's read-side view both hold.
    def kept(nanoseconds: int | None) -> int | None:
        return None if nanoseconds is None or not possible(int(nanoseconds)) else int(nanoseconds)

    birth = kept(entry.created_ns if str(entry.created_time_source) == "birthtime" else None)
    modified = kept(entry.modified_ns)
    filesystem: tuple[int, str] | None = None
    if birth is not None and modified is not None:
        filesystem = (modified, "modified_before_born") if birth > modified else (birth, "birth")
    elif modified is not None:
        filesystem = (modified, "modification")
    elif birth is not None:
        filesystem = (birth, "birth")
    return content, filesystem


def _instant_of(nanoseconds: int) -> str:
    """An instant as ISO 8601 in UTC, to the second."""
    import datetime as dt

    return dt.datetime.fromtimestamp(nanoseconds / 1e9, tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FormatFromArchiveMembers:
    """A ZIP is a program's own archive where every member is that program's file and says so.

    A ZIP's name does not prove that it holds Fiji regions, so the predicate reads what a dive
    found inside the archive. Fiji's ROI Manager saves a set of regions as a ZIP whose
    every member is a ``.roi`` beginning with ImageJ's header. A dive lists an archive's members
    and reads each one's first bytes in memory (the archive reading, version 2). This predicate
    reads what the dive recorded. Every member must carry one of ``member_extensions``, and every
    member must begin with the header that the observation ``header_observation`` counts. So a ZIP
    of other programs' ``.roi`` files is not taken for one, and a listing that stopped short
    licenses no statement about every member. The predicate concludes ``format``, a sense's term.

    A name like ``RoiSet.zip`` is weaker evidence. It is asked about and never concluded here:
    that is ``archive_named_but_unlisted``'s.
    """

    name = "format_from_archive_members"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the format where every listed member is the program's own, or None."""
        listing = _archive_listing(subject, parameters)
        if listing is None or listing.state != "complete" or listing.members == 0:
            return None
        if set(listing.extensions) - _names(parameters, "member_extensions"):
            return None
        if listing.headers != listing.members:
            return None
        return Concluded(values=(str(parameters["format"]),), inputs=listing.cited)

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether nobody listed its members, or which of them are not the program's."""
        listing = _archive_listing(subject, parameters)
        if listing is None:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED, reason="no dive has listed its members"
            )
        others = sorted(set(listing.extensions) - _names(parameters, "member_extensions"))
        if listing.state != "complete":
            reason = f"its listing stopped short ({listing.state}), so no member is vouched for"
        elif listing.members == 0:
            reason = "it holds no member"
        elif others:
            kinds = ", ".join(f".{other}" if other else "no extension" for other in others)
            reason = f"it holds members of other kinds ({kinds})"
        else:
            reason = (
                f"{listing.headers} of its {listing.members} members begin with the header "
                "the rule asks for"
            )
        return Abstention(inputs=listing.cited, reason=reason)


@dataclass(frozen=True, slots=True)
class _ArchiveListing:
    """What a dive recorded of an archive's members."""

    state: str
    members: int
    extensions: dict[str, int]
    headers: int
    cited: tuple[str, ...]


def _archive_listing(subject: object, parameters: Mapping[str, object]) -> _ArchiveListing | None:
    """The archive reading's record of one entry, or None where no dive listed it."""
    header_key = str(parameters.get("header_observation") or "")
    held = getattr(subject, "observations", subject)
    found = {
        observation.key: observation
        for observation in (held if isinstance(held, (list, tuple)) else ())
        if isinstance(observation, Observation)
    }
    listed = found.get("archive.member_extensions")
    if listed is None or listed.value_structure is None:
        return None
    count = found.get("archive.member_count")
    state = found.get("archive.expansion_state")
    headers = found.get(header_key) if header_key else None
    kinds = json.loads(listed.value_structure)
    return _ArchiveListing(
        state=str(state.value_text) if state is not None else "unknown",
        members=int(count.value_integer or 0) if count is not None else 0,
        extensions={str(key): int(value) for key, value in kinds.items()},
        headers=int(headers.value_integer or 0) if headers is not None else 0,
        cited=tuple(
            sorted(
                observation.observation_id
                for observation in (listed, count, state, headers)
                if observation is not None
            )
        ),
    )


@declare(
    "format_from_archive_members",
    version="1",
    summary="Classify an archive as a program's own where every member it lists is that program's",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.OBSERVATIONS,
    parameters=("member_extensions", "header_observation", "format"),
    support_families=("type_detection",),
)
def _build_format_from_archive_members() -> Predicate:
    return FormatFromArchiveMembers()


@declare(
    "earliest_credible_time",
    version="3",
    summary="Classify when a file first existed, naming the record that says so",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.RECORDS,
    parameters=(
        "earliest_credible_year",
        "future_tolerance_seconds",
        "template_stamp_minimum_gap_days",
        "template_stamp_observers",
    ),
    support_families=("clock_agreement",),
    # Its records are read for the content's clocks and the dive's readings, never the name.
    reads_no_name=True,
)
def _build_earliest_credible_time() -> Predicate:
    return EarliestCredibleTime()


@declare(
    "placeholder_from_entry_kind",
    version="1",
    summary="Classify an entry that stands in for content held elsewhere",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.ENTRY,
    parameters=("detect_stubs", "minimum_declared_bytes"),
    support_families=("filesystem_fact",),
)
def _build_placeholder_from_entry_kind() -> Predicate:
    return PlaceholderFromEntryKind()


@declare(
    "class_from_enclosing_folder",
    version="1",
    summary="Classify a file from what its folder states and it does not restate",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.RECORDS,
    parameters=("maximum_hops", "theme_kind"),
    support_families=("inherited_context",),
)
def _build_class_from_enclosing_folder() -> Predicate:
    return ClassFromEnclosingFolder()


@declare(
    "naming_is_authored_or_emitted",
    version="1",
    summary="Classify whether a person typed a name or a machine generated it",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.OBSERVATIONS,
    parameters=("minimum_siblings",),
    support_families=("name_structure", "sibling_agreement"),
)
def _build_naming_is_authored_or_emitted() -> Predicate:
    return NamingIsAuthoredOrEmitted()


class NameDateNeighbourhood:
    """The dates a name gives an entry: a neighborhood, descriptive, never a clock.

    A date in a folder title is a general "date" neighborhood. It is different from the file's
    actual date, and a date in a file or folder name is more descriptive than exact. A folder's
    date and a file name's date code each give a date neighborhood for the file.

    So the value is a neighborhood, in canonical JSON: ``{"from", "to", "source"}`` and, where it
    came from a folder, ``"folder"``. The entry's own name is read first. One date is a day's
    neighborhood, a month (``2024-03``) is its month, and two or more dates give the span from the
    first to the last. Otherwise, where ``inherit_from_folders`` is set, the nearest folder above
    whose name holds a date gives the neighborhood. Nothing here is an event time.
    ``earliest_credible_time`` still reads the file's own content and filesystem clocks, and this
    predicate is read beside it and never over it.
    """

    name = "name_date_neighbourhood"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the neighborhood the entry's name, or its folder's, gives it, else None."""
        entry = getattr(subject, "entry", None)
        if entry is None:
            return None
        own = tuple(getattr(subject, "own", ()))
        inherited = tuple(getattr(subject, "inherited", ()))
        said: dict[str, object]
        if own:
            said = {**_span(own), "source": "name"}
        elif parameters.get("inherit_from_folders", True) is True and inherited:
            said = {
                **_span(inherited),
                "source": "folder",
                "folder": str(getattr(subject, "folder", "")),
            }
        else:
            return None
        return Concluded(values=(canonical_json(said),), inputs=(entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that neither its name nor a folder above it holds a date."""
        return Abstention(reason="neither its name nor a folder's above it holds a date")


def _span(dates: tuple[str, ...]) -> dict[str, str]:
    """The first and last day the dates cover: a month covers its whole month."""
    import calendar

    firsts: list[str] = []
    lasts: list[str] = []
    for date in dates:
        if len(date) == 7:
            year, month = int(date[:4]), int(date[5:7])
            firsts.append(f"{date}-01")
            lasts.append(f"{date}-{calendar.monthrange(year, month)[1]:02d}")
        else:
            firsts.append(date)
            lasts.append(date)
    return {"from": min(firsts), "to": max(lasts)}


@declare(
    "name_date_neighbourhood",
    version="1",
    summary="Classify the date neighborhood an entry's name, or its folder's, gives it",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.NAME_DATES,
    parameters=("inherit_from_folders",),
    support_families=("name_structure",),
)
def _build_name_date_neighbourhood() -> Predicate:
    return NameDateNeighbourhood()
