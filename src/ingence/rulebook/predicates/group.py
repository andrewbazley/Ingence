"""Predicates that define an equivalence relation, and therefore a grouping.

Every grouping states its respect. ``equivalent`` is the only primitive that requires one,
because "these are the same" without saying in what respect is the generic relation that the
engine refuses to have. A grouping rule's respect is what a window, a level and a completeness
check are all computed against.

A grouping is a quotient, and a quotient needs an equivalence. So a rule here may only name a
primitive that is symmetric and transitive. The loader checks this against the declared signature
and not against a list of the two primitives that qualify. The reason is that ``concerns`` is
symmetric and never transitive, and it is exactly what somebody would reach for when grouping by
shared subject matter.

Each predicate returns the class that a subject belongs to, as a key. It never returns whether
two subjects stand in the relation. Two subjects are equivalent in the rule's respect exactly
where their keys are equal. The kernel of a function is an equivalence, so symmetry and
transitivity within the respect hold by construction, and no pair is ever compared. A subject
that the rule has nothing to say about returns no key and joins no class. Turning keys into
classes is the quotient, in :mod:`ingence.grouping.quotient`. Keeping the two apart lets the
relation be stated, argued with and hashed before anything computes a partition from it.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ingence.core.ids import CaseSensitivity, canonical_json
from ingence.core.outcomes import IdentityScope
from ingence.core.schema import (
    WHOLE_DIGEST_UNCHECKED_MOVES,
    Component,
    DictionaryBinding,
    Entry,
    FailureClass,
    Observation,
    RuleEffect,
    SourceScope,
)
from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.registry import (
    Abstention,
    ClassKey,
    Predicate,
    PredicateNotEvaluableError,
    Reads,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "SameComponentValues",
    "SameComposition",
    "SameContentDigest",
    "SameExtractedShape",
    "SameObservedMetadata",
]

#: What the census records about a folder's direct children.
_COMPOSITION = "folder.composition_digest"
_CHILD_COUNT = "folder.child_count"


class SameComposition:
    """Folders whose direct children have the same shape composition are equivalent
    in respect composition, and in no other respect.

    The respect is narrow on purpose. The census records a composition digest per folder. The
    recurrence is real and bimodal: on real folders, about half are compositionally unique and
    most others sit in groups of four or more. That is enough to assert an equivalence in respect
    composition and not enough to assert an ``experiment type``.

    The two differ in the two words that do all the work. An experiment type is partitioning, so a
    thing belongs to exactly one. It also drives completeness, so the engine says what is missing.
    A wrong partition does not produce a weak claim. It produces a confident claim that a file is
    absent from a set it was never part of, which is the failure that users forgive least. Per
    folder may be the wrong unit, and real folder trees are mixtures, so the validation that would
    license the stronger claim has not happened.

    Declaring the weaker relation gives the Record something to collect human confirmations
    against. That evidence could eventually license the stronger relation.

    The key is the composition digest that the census records for the folder, where the folder
    holds at least the rule's minimum of children. A folder with fewer children matches many
    folders by accident and has no key.
    """

    name = "same_composition"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class of folders composed like this one, where it holds enough children."""
        digests, children = _composition(subject)
        if len({digest for _, digest in digests}) != 1 or not children:
            return None
        if min(count for _, count in children) < _integer(parameters["minimum_children"]):
            return None
        return ClassKey(
            key=digests[0][1],
            inputs=tuple(sorted(identifier for identifier, _ in [*digests, *children])),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the folder was never described, reads two ways, or holds too few children."""
        digests, children = _composition(subject)
        inputs = tuple(sorted(identifier for identifier, _ in [*digests, *children]))
        if not digests:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason="folder characterisation has not described this folder",
            )
        if len({digest for _, digest in digests}) > 1:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=inputs,
                reason="the folder carries more than one composition",
            )
        if not children:
            return Abstention(inputs=inputs, reason="the folder's child count was not recorded")
        minimum = _integer(parameters["minimum_children"])
        return Abstention(inputs=inputs, reason=f"the folder holds fewer than {minimum} children")


class SameContentDigest:
    """Two entries whose full content digests agree are the same bytes.

    This is the only substitutive relation in the engine, and the only grouping that licenses
    treating one member as a stand-in for another. Everything downstream that counts a pool's real
    size, collapses duplicates or reports recovered space rests on it.

    A digest of part of a file is not a digest of the file, and the engine holds both. The reuse
    model computes paired fingerprints over sampled regions. That is a cheap and correct test for
    whether something changed. Promoting one to an identity claim would assert equality from a
    sample, and a constraint refuses that separately.

    Agreement on a digest is a fact, and the grouping is defeasible in one direction only. Two
    files that agree are identical. Two files that disagree are verified different. That is a
    positive finding and not an absence, and it is worth recording as one.

    The key is the digest that one named identity method recorded, together with the scope it
    read, because a copy claim rests on content. A method's name and the scope it read, the whole
    file or a sample of it, are both in the key. So a sampled digest never groups with a whole
    one, and no consumer can mistake which it has. A rule that names the sampled method says in
    its own sentence that it nominates and does not prove. The shipped rule here names a
    whole-file method and is the one grouping at the entailed tier.

    Two digests of one file that disagree are a conflict and not a class. One entry that carries
    two values under one method means two runs read different bytes under one identifier. Grouping
    on either value would assert an equality that nothing established.

    The rule is evaluable wherever a dive computed the digest that the rule names. It is
    unexamined wherever no dive did, because a census reads no content.

    Since version 3, the rule does not read a whole-file digest that a walk carried across a move
    until a dive has checked it. A walk matches a file at a new path to one that left, without
    reading either. It gives the new file the readings of the departed one, including the
    whole-file digest. Until a dive compares that digest with the file where it lies, it is a
    digest of the file that left. Proving two files identical on it asserts byte equality that
    nobody checked. The entry's ``moved_by`` says whether a dive has checked it. It names the
    digest that confirmed the move. A move that no dive has confirmed, or that only a sampled digest
    has, leaves a carried whole-file digest unchecked. A digest that a dive computed at the new
    path is the file's own and is read whatever the move says. The run tells the two apart: the
    run of a digest computed at the new path is not the walk's. A sampled digest nominates and is
    not held to this.
    """

    name = "same_content_digest"
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class of entries whose named digest agrees, or None where none was read."""
        found = _digests(subject, parameters)
        if len({observation.value_text for observation in found}) != 1:
            return None
        recorded = found[0]
        if _carried_unchecked(subject, found):
            return None
        return ClassKey(
            key=canonical_json(
                {
                    "method": recorded.identity_method,
                    # The scope the method read: a sampled digest never groups with a whole
                    # one, and an observation recording none is its own key rather than
                    # either.
                    "read": recorded.identity_scope.value if recorded.identity_scope else "",
                    "digest": recorded.value_text,
                }
            ),
            inputs=tuple(sorted(observation.observation_id for observation in found)),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether no dive computed this digest, or two readings of it disagree."""
        found = _digests(subject, parameters)
        if not found:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason=(
                    f"no dive recorded a {_digest_method(parameters)} digest for this entry, "
                    f"and a census reads no content"
                ),
            )
        if len({observation.value_text for observation in found}) == 1 and _carried_unchecked(
            subject, found
        ):
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                inputs=tuple(sorted(observation.observation_id for observation in found)),
                reason=(
                    "this file moved and its whole-file digest has not been checked since: "
                    "it was read from the file at its old path"
                ),
            )
        return Abstention(
            status=ResolutionStatus.EXAMINED_CONFLICTING,
            failure_class=FailureClass.INTERPRETATION,
            inputs=tuple(sorted(observation.observation_id for observation in found)),
            reason="two readings of this digest disagree, so nothing about the bytes is settled",
        )


class SameObservedMetadata:
    """Two entries agreeing on every observed filesystem fact, and on nothing else.

    The facts are the name, the size, both clocks and the entry type. This is what a copy looks
    like from outside. Saying so without reading a byte is the whole value of the rule. A pool
    with ten thousand suspected duplicates can be narrowed to the few hundred worth hashing. That
    is the difference between a content pass that finishes and one that nobody runs.

    The rule is an equivalence in respect metadata, and it is not identity. A schema could call
    these entries duplicate groups and set a flag beside them that says the content is unverified.
    That is one column that a consumer can forget to read. Here the two are different relations
    with different primitives, so forgetting is not available. Metadata agreement groups and
    substitutes nothing. Byte equality substitutes.

    The relation is real even where the files differ. Two files that agree on every observable
    and differ in content are in a genuine and interesting state, and they are not a false
    positive. The flag that raises them for verification is a separate rule that reads this same
    relation.

    The rule is evaluable on an entry beside its root, a shape of its own. So the pass's guards
    about names never read into a rule that compares no name token. A name is compared normalized
    to NFC, and case-folded only where the root's filesystem is known to ignore case. A root whose
    sensitivity is unknown compares names as stored, which never merges two genuinely distinct
    files. A fact that the entry does not record is abstention, because absence on both sides is
    not agreement. A fact that this predicate cannot read makes the rule not evaluable, naming it.
    """

    name = "same_observed_metadata"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class of entries agreeing on every compared fact."""
        facts = _compared_facts(parameters)
        entry = getattr(subject, "entry", None)
        if not isinstance(entry, Entry):
            return None
        observed = _observed(entry, getattr(subject, "root", None), facts)
        if observed is None:
            return None
        return ClassKey(key=canonical_json(observed), inputs=(entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say which compared fact the entry does not record."""
        facts = _compared_facts(parameters)
        entry = getattr(subject, "entry", None)
        if not isinstance(entry, Entry):
            return Abstention(reason="handed something other than an entry beside its root")
        absent = ", ".join(fact for fact in facts if getattr(entry, fact) is None)
        return Abstention(
            inputs=(entry.entry_id,),
            reason=f"the entry records no {absent}, and absence on both sides is not agreement",
        )


class SameComponentValues:
    """Two entries whose names agree on every component of a stated kind.

    One mechanism serves four different equivalences, and the respect separates them. Agreement on
    quantities and status words is a shared experimental condition. Agreement on an indexed label
    is a shared acquisition. Agreement on the stem after markers are stripped is a shared name
    family. Agreement on everything but a version marker is a version family. These are four
    propositions with one comparison. The rule file, where a person can read it, says which
    components count.

    The comparison is over normalized values. ``40min`` and ``2400s`` reduce to one duration, and
    ``Tg500nM`` and ``tg500nm`` to one concentration. So two names written by two people in one
    laboratory reach the same key without either of them changing how they type. Comparing
    spellings would find differences that are orthographic and not experimental. The normalizer
    exists to prevent that.

    Absence on both sides is not agreement. Two files that name no condition are not thereby in the
    same condition, and a relation that said so would put every unnamed file in one enormous
    class. The rule carries the minimum number of components that must agree, and a name below the
    floor has no key.

    The key is a canonical list of the kind and normalized value of every component of the stated
    kinds, so a word and a number that are spelled alike are different components. Only components
    read off a name count, because every rule that uses this predicate compares names.

    Two optional filters read what the Dictionary made of a word. With neither declared, the key is
    exactly version 1's. Version 2 reads an entry's records and not its components alone, because
    the filters need the bindings beside them.

    - ``ignored_theme_kinds`` leaves out a word whose binding resolved to a sense of one of those
      kinds, and an indexed component whose label did. A word that the Dictionary could not settle
      stays in, even where a sense it stood between is of an ignored kind, because leaving it out
      would decide the conflict. Ignoring ``stage`` lets a raw file and the file processed from it
      share a name family. Ignoring ``replicate`` lets ``rep1`` and ``rep2`` of one experiment
      share an identity.
    - ``counted_theme_kinds`` counts a word only where its binding resolved to a sense of one of
      those kinds, and leaves every other kind of component as it is. Counting ``condition`` and
      ``reagent`` keeps a shared condition to the words that name one. The binding that let a word
      count is an input of the key, since it is half of why the key is what it is.

    The filters decide which components count and never what they mean. The key still compares
    normalized words, so ``mock`` and ``untreated`` stay in two classes though both bind
    ``control``.

    Only ``ignored_theme_kinds`` reads an index, and the asymmetry has a reason. Ignoring a kind
    removes a distinction, and a replicate index is exactly a distinction that an experiment key
    should not make. Counting names the whole basis of a key, and an index carries a position and
    not a meaning. Two doses written ``d1`` and ``d2`` are different conditions, so dropping every
    index from a counted key would merge things that differ. So ``counted_theme_kinds`` reads words
    alone, as in version 2, and the shipped condition rule keeps its version 2 key.

    A word that the binder never examined cannot be filtered. Under either filter, a name that
    carries one has no key and the rule is unexamined for it. The rule does not hold a key that
    kept or dropped a word that nobody read.

    A rule may keep the positions of some kinds. With ``ordered_component_kinds`` naming any kind,
    the key carries every occurrence of those kinds in the order the name wrote them, beside the set
    of values that it carries without this setting. A key that treats a name's numbers as a set
    makes a plate reader's ``A1_03_1_2_DAPI_001`` and ``A1_03_1_3_DAPI_002`` one identity. They have
    the same numbers in different places, which is a different tile and a different field. Positions
    come from the name's own slots, one per occurrence, since a component is deduplicated by value
    and carries no position. A run whose name layouts were not recorded cannot be keyed this way,
    and the rule is unexamined for it. The rule does not hold a key that quietly forgot the
    positions.

    A rule may compare names within one folder. With ``compared_within`` set to ``folder``, the
    key carries the entry's own parent, so names that agree in two folders are two classes. The
    class that a proposing rule draws its pairs from is then the class it cuts. A pair constraint
    that checked the folder could only block what the cut had kept, because the cut keeps
    counterparts in identifier order before any constraint runs. A container's members compare
    within their container, which is their parent. Set to ``pool``, or left out, the key is exactly
    version 2's. Under ``folder``, an entry that no folder holds has no key, and the entry is an
    input, since its parent is half of why its key is what it is.

    Version 6 adds a comparison within an experiment. One experiment's chain is commonly split
    across its stage subfolders (``raw/``, ``processed/``, ``analysis/``), and a key that carries
    the parent never draws such a pair. With ``compared_within`` set to ``experiment``, a file
    that the pass placed beneath an experiment (:class:`~ingence.infer.subjects.ExperimentPlace`)
    is keyed on the experiment's folder and on what the folders between it and the file are
    called. Each folder's name is read as a file's is. A word or an indexed label bound to a sense
    of kind ``ignored_subfolder_theme_kinds`` is left out (``stage`` where the rule leaves it out).
    So ``raw/`` and ``processed/`` are one place, and ``day1/`` and ``day2/`` are two. A file
    beneath no experiment is keyed on its folder, as under ``folder``. The experiment's claims are
    inputs. The folders' records are about other entries and are not inputs.
    """

    name = "same_component_values"
    version = "6"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class of names agreeing on every counted component of the stated kinds."""
        counted = _counted_components(subject, parameters)
        if counted.unexamined:
            return None
        values = sorted(
            {
                (component.component_type, component.normalized_value)
                for component in counted.components
            }
        )
        if not values or len(values) < _integer(parameters["minimum_components"]):
            return None
        inputs = (
            *(component.component_id for component in counted.components),
            *(binding.binding_id for binding in counted.bindings),
        )
        ordered_kinds = _optional_strings(parameters, "ordered_component_kinds")
        keyed: dict[str, object] = {}
        if ordered_kinds:
            positions = _positions(subject, counted.components, ordered_kinds)
            if positions is None:
                return None
            keyed["ordered"] = positions
        compared = _compared_within(parameters)
        if compared == _ACROSS_THE_POOL:
            return ClassKey(
                key=canonical_json({"components": values, **keyed} if keyed else values),
                inputs=inputs,
            )
        entry = _entry_of(subject)
        if entry is None or entry.parent_entry_id is None:
            return None
        place = (
            getattr(subject, "experiment_place", None)
            if compared == _WITHIN_AN_EXPERIMENT
            else None
        )
        if place is not None:
            return ClassKey(
                key=canonical_json(
                    {
                        "components": values,
                        **keyed,
                        "experiment": place.experiment,
                        "between": _between(place, parameters),
                    }
                ),
                inputs=(*inputs, entry.entry_id, *place.claims),
            )
        return ClassKey(
            key=canonical_json({"components": values, **keyed, "folder": entry.parent_entry_id}),
            inputs=(*inputs, entry.entry_id),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether a word went unexamined, or the name counts no component, or too few."""
        counted = _counted_components(subject, parameters)
        if counted.unexamined:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                inputs=tuple(component.component_id for component in counted.unexamined),
                reason="a filter reads a word's sense, and the binder never examined the word",
            )
        if (
            _optional_strings(parameters, "ordered_component_kinds")
            and _positions(
                subject,
                counted.components,
                _optional_strings(parameters, "ordered_component_kinds"),
            )
            is None
        ):
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                inputs=tuple(component.component_id for component in counted.components),
                reason=(
                    "the rule keys on where a name's components stand, and this run recorded "
                    "no layout for this name"
                ),
            )
        counts = " that its filters count" if counted.filtered else ""
        if not counted.components:
            return Abstention(reason=f"the name carries no component of the stated kinds{counts}")
        minimum = _integer(parameters["minimum_components"])
        compared = {
            (component.component_type, component.normalized_value)
            for component in counted.components
        }
        if len(compared) >= minimum and _compared_within(parameters) != _ACROSS_THE_POOL:
            return Abstention(
                inputs=tuple(component.component_id for component in counted.components),
                reason=(
                    "the rule compares names within one folder, and no folder holds this entry"
                ),
            )
        return Abstention(
            inputs=tuple(component.component_id for component in counted.components),
            reason=f"the name carries fewer than {minimum} components of the stated kinds{counts}",
        )


class NamedForAnAcquisition:
    """Files grouped by the acquisition each is named for, the acquisition among them.

    A tool names what it writes after the movie it read, with words of its own beside it
    (``zorp_kelt_001_results.csv`` from ``kelt_001.nd2``). A name identity cannot join the two,
    because the tool's words are not stage words. On two real copies, no identity class held both
    an acquisition and an analysis. The pass says which acquisition a file's name holds whole,
    nearest it in its experiment (:func:`~ingence.infer.experiment_places.acquisitions_named`), and
    the key is that acquisition. The acquisition's own claim is an input. The pass reads
    ``minimum_key_characters``: the fewest letters and digits that an acquisition's name needs to
    be matched on.
    """

    name = "named_for_an_acquisition"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class of the acquisition the file is named for, or None."""
        named = getattr(subject, "named_acquisition", None)
        entry = _entry_of(subject)
        if named is None or entry is None:
            return None
        minimum = parameters.get("minimum_key_characters")
        if isinstance(minimum, int) and named.characters and named.characters < minimum:
            return None  # matched at a smaller minimum another rule named
        return ClassKey(
            key=canonical_json({"acquisition": named.acquisition}),
            inputs=tuple(sorted({entry.entry_id, named.claim} - {""})),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that the file is not an acquisition and names none it could be matched to."""
        del subject, parameters
        return Abstention(
            reason=(
                "the file is no acquisition, and its name holds no acquisition's name, of this "
                "rule's minimum length, held once and nearest it in its experiment or folder"
            )
        )


class NamedBeside:
    """Files grouped by a class the pass formed from names beside one another.

    ``attribute`` names which: ``named_series``, a series of numbered files and the table named for
    it in their folder (``zeph_001.nd2`` ... and ``zeph_results.xlsx``); ``named_beside``, one whole
    name in sibling folders (``images/a.tif``, ``masks/a.tif``). The pass computes the class from
    the files' names and the Dictionary's senses of their words, the settings it reads declared by
    the rule; the key says which members a proposing rule pairs.
    """

    def __init__(self, name: str, attribute: str, abstains: str) -> None:
        self.name = name
        self.version = "1"
        self._attribute = attribute
        self._abstains = abstains

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class the pass placed the file in, or None."""
        del parameters
        key = getattr(subject, self._attribute, None)
        entry = _entry_of(subject)
        if not key or entry is None:
            return None
        return ClassKey(key=str(key), inputs=(entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say what the file is not."""
        del subject, parameters
        return Abstention(reason=self._abstains)


class SameExtractedShape:
    """Two files whose stored structure digest agrees, in one stated structural respect.

    Two respects exist, told apart by which floor the rule declares. ``minimum_columns`` makes the
    rule a column respect: the digest that the dive stored for a delimited table
    (``identity.tabular_schema``), which holds the normalised column names, their order and the
    delimiter. ``minimum_dimensions`` makes it an array respect: the digest stored for a NumPy
    array (``identity.array_signature``), which holds the shape, the element type, the storage
    order and the number of dimensions.

    The digest holds no column types, on purpose. A type is a guess over sampled rows, and two
    exports of one table would split on which rows were sampled.

    Structure is the respect where an equivalence is nearly free. The dive already computed the
    digest, and the digest is independent of every naming convention, so it means something on a
    pool whose vocabulary the Dictionary has never seen. Agreement on columns is not agreement on
    an experiment. Two tables with one set of columns may come from one instrument's export, from
    one analysis, or from two unrelated studies that used the same program. The respect says only
    "same columns". The relation licenses nothing else.

    The floor is read from the stored count (``table.column_count`` for a table,
    ``array.dimension_count`` for an array), so a one-column table, which matches many tables by
    accident, has no key.

    Abstention is explained in three ways. A file that no dive read is unexamined. A file that a
    dive read and that holds no digest has nothing to say (it is not a table of two columns or
    more, or not a stored array). A file below the floor, or whose digest or count reads two ways,
    says so.
    """

    name = "same_extracted_shape"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class of files sharing a digest, where it was read and reaches the floor."""
        shape = _shape_respect(parameters)
        digests = _latest(subject, shape.digest_key)
        if not digests or len({item.value_text for item in digests}) != 1:
            return None
        counts = _latest(subject, shape.count_key)
        if not counts or len({item.value_integer for item in counts}) != 1:
            return None
        count = counts[0].value_integer
        if count is None or count < _integer(parameters[shape.floor]):
            return None  # no number recorded, or under the floor
        return ClassKey(
            key=canonical_json(
                {
                    "respect": shape.respect,
                    # The method and the digest, not the method's version: a table read by
                    # version 1 and one read by version 2 store one digest for one header, and
                    # a project dived by two builds must group them together. (An array's two
                    # versions store different content, so their digests cannot meet.)
                    "method": digests[0].identity_method,
                    "digest": digests[0].value_text,
                }
            ),
            inputs=tuple(sorted(item.observation_id for item in (*digests, *counts))),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the file was not read, holds none, reads two ways or is below the floor."""
        shape = _shape_respect(parameters)
        digests = _latest(subject, shape.digest_key)
        counts = _latest(subject, shape.count_key)
        inputs = tuple(sorted(item.observation_id for item in (*digests, *counts)))
        if not digests:
            if not _held_observations(subject, _READ_KEY):
                return Abstention(
                    status=ResolutionStatus.UNEXAMINED,
                    reason=f"no dive read this file, and a census reads no {shape.thing}",
                )
            return Abstention(reason=f"the dive read this file and found no {shape.thing} in it")
        if len({item.value_text for item in digests}) != 1:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=inputs,
                reason=f"two readings of this file's {shape.thing} disagree",
            )
        if not counts:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                inputs=inputs,
                reason=(
                    f"its {shape.thing} could be read, and no number of {shape.counted}s was "
                    f"recorded to hold it to the rule's minimum"
                ),
            )
        if len({item.value_integer for item in counts}) != 1:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=inputs,
                reason=f"two readings of this file's number of {shape.counted}s disagree",
            )
        if counts[0].value_integer is None:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                inputs=inputs,
                reason=f"the number of {shape.counted}s recorded for this file is not a number",
            )
        minimum = _integer(parameters[shape.floor])
        unit = shape.counted if minimum == 1 else f"{shape.counted}s"
        return Abstention(inputs=inputs, reason=f"the file has fewer than {minimum} {unit}")


@dataclass(frozen=True, slots=True)
class _ShapeRespect:
    """What one structural respect reads: its digest, its count and its floor."""

    respect: str
    #: The rule parameter holding the floor.
    floor: str
    digest_key: str
    count_key: str
    #: What the digest describes, and what its count counts, in words.
    thing: str
    counted: str


_COLUMNS = _ShapeRespect(
    respect="columns",
    floor="minimum_columns",
    digest_key="identity.tabular_schema",
    count_key="table.column_count",
    thing="column names",
    counted="column",
)
_DIMENSIONS = _ShapeRespect(
    respect="array",
    floor="minimum_dimensions",
    digest_key="identity.array_signature",
    count_key="array.dimension_count",
    thing="array shape",
    counted="dimension",
)

#: A reading every dive writes for a file it opened, so a file without one was never opened.
_READ_KEY = "type.media_type"


def _shape_respect(parameters: Mapping[str, object]) -> _ShapeRespect:
    """Return the respect a rule states by its floor, refusing a rule stating none or both."""
    stated = [shape for shape in (_COLUMNS, _DIMENSIONS) if shape.floor in parameters]
    if len(stated) != 1:
        message = (
            "same_extracted_shape groups by column names or by array shape, and a rule says which "
            "by declaring minimum_columns or minimum_dimensions, one of them"
        )
        raise PredicateNotEvaluableError(message)
    return stated[0]


def _held_observations(subject: object, key: str | None = None) -> tuple[Observation, ...]:
    """Return the observations a subject holds, of one key if one is named, in canonical order."""
    return tuple(
        sorted(
            (
                item
                for item in _records_of(subject)
                if isinstance(item, Observation) and (key is None or item.key == key)
            ),
            key=lambda observation: observation.observation_id,
        )
    )


def _version_order(observation: Observation) -> tuple[int, str]:
    """Order observers' versions numerically where they are numbers, else as written."""
    version = observation.observer_version
    return (int(version), "") if version.isdigit() else (-1, version)


def _latest(subject: object, key: str) -> tuple[Observation, ...]:
    """Return the readings of one key made by the newest version of the observer that made them.

    A dive under a newer engine writes beside the readings of an older one, and a digest of
    version 1 and one of version 2 are different digests of one file, not a disagreement.
    """
    found = _held_observations(subject, key)
    if not found:
        return ()
    newest = max(_version_order(item) for item in found)
    return tuple(item for item in found if _version_order(item) == newest)


#: How a digest observation is keyed, per identity method that ran (``scan/deepen.py``).
_IDENTITY = "identity."


def _digest_method(parameters: Mapping[str, object]) -> str:
    """Return the identity method a digest grouping names, refusing a rule naming none."""
    method = str(parameters.get("digest_algorithm") or "")
    if not method:
        message = (
            "same_content_digest keys an entry by the digest one identity method recorded, "
            "and this rule names none"
        )
        raise PredicateNotEvaluableError(message)
    return method


def _digests(subject: object, parameters: Mapping[str, object]) -> list[Observation]:
    """Return the digest observations one named method recorded for this entry, canonically."""
    key = f"{_IDENTITY}{_digest_method(parameters)}"
    held = getattr(subject, "observations", None)
    return sorted(
        (
            item
            for item in (held if held is not None else _records_of(subject))
            if isinstance(item, Observation) and item.key == key and item.value_text
        ),
        key=lambda observation: observation.observation_id,
    )


def _carried_unchecked(subject: object, digests: Sequence[Observation]) -> bool:
    """Whether every whole-file digest on this entry was carried across a move no dive has checked.

    Only a carried digest is refused. A walk gives the file at its new path the readings of the
    file that left, under the walk's own run, and the entry's first run is that walk's. A digest
    that a dive computed at the new path is in a later run, is the new file's own, and stands
    whatever the move says. One such digest among the file's is enough.

    A subject that does not hold its entry (a bare sequence of observations) says nothing of a
    move, and its digests stand.
    """
    entry = getattr(subject, "entry", None)
    if not isinstance(entry, Entry) or entry.moved_by not in WHOLE_DIGEST_UNCHECKED_MOVES:
        return False
    return all(
        digest.identity_scope in (IdentityScope.WHOLE, None)
        and digest.run_id == entry.first_seen_run
        for digest in digests
    )


def _integer(value: object) -> int:
    """Return an integer parameter as the loader stored it, refusing anything else."""
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"expected an integer parameter, found {value!r}"
        raise TypeError(message)
    return value


def _strings(value: object) -> frozenset[str]:
    """Return a string-list parameter as the set of its values, refusing anything else."""
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return frozenset(value)
    message = f"expected a list of strings, found {value!r}"
    raise TypeError(message)


#: The filesystem facts an entry records that a metadata grouping can compare. A fact
#: outside these makes the rule not evaluable, naming it, rather than silently equal.
_OBSERVED_FACTS = frozenset(
    {"name", "extension", "entry_type", "size_bytes", "created_ns", "modified_ns", "changed_ns"}
)


def _compared_facts(parameters: Mapping[str, object]) -> tuple[str, ...]:
    """Return the facts a metadata grouping compares, refusing one no entry records."""
    facts = tuple(sorted(_strings(parameters["compared_facts"])))
    unknown = [fact for fact in facts if fact not in _OBSERVED_FACTS]
    if unknown:
        message = (
            f"same_observed_metadata compares {', '.join(unknown)}, which is not an "
            f"observed filesystem fact it can read"
        )
        raise PredicateNotEvaluableError(message)
    return facts


def _observed(
    entry: Entry, root: object, facts: tuple[str, ...]
) -> list[tuple[str, object]] | None:
    """Return the compared facts as a key compares them, or None where one is absent."""
    observed: list[tuple[str, object]] = []
    for fact in facts:
        value = getattr(entry, fact)
        if value is None:
            return None
        if fact == "name":
            value = unicodedata.normalize("NFC", str(value))
            if getattr(root, "case_sensitivity", None) is CaseSensitivity.INSENSITIVE:
                value = value.casefold()
        elif isinstance(value, Enum):
            value = value.value
        observed.append((fact, value))
    return observed


def _optional_strings(parameters: Mapping[str, object], name: str) -> frozenset[str]:
    """Return a string-list parameter a rule may leave undeclared, as nothing where it does."""
    declared = parameters.get(name)
    return frozenset() if declared is None else _strings(declared)


#: Where a name identity is compared: across the data pool, within one folder, or within one
#: experiment, across its subfolders.
_ACROSS_THE_POOL = "pool"
_WITHIN_ONE_FOLDER = "folder"
_WITHIN_AN_EXPERIMENT = "experiment"

#: What a folder between an experiment and a file is read without, where a rule says nothing.
_SUBFOLDER_KINDS_IGNORED = ("stage",)


def _compared_within(parameters: Mapping[str, object]) -> str:
    """Where a rule compares names, refusing a scope no key can carry."""
    declared = parameters.get("compared_within")
    if declared is None or declared == _ACROSS_THE_POOL:
        return _ACROSS_THE_POOL
    if declared in (_WITHIN_ONE_FOLDER, _WITHIN_AN_EXPERIMENT):
        return str(declared)
    message = (
        f"same_component_values compares names across the pool, within one folder or within one "
        f"experiment, and this rule asks for {declared!r}"
    )
    raise PredicateNotEvaluableError(message)


def _between(place: object, parameters: Mapping[str, object]) -> list[list[list[str]]]:
    """What each folder between an experiment and a file is called, as a key compares it.

    Each folder's name components of the rule's kinds, leaving out a word or an indexed label
    bound to a sense of kind ``ignored_subfolder_theme_kinds``. A word the binder never examined is
    kept, since leaving it out would decide what it means. A folder left with nothing (``raw``,
    ``processed``) is not a distinction and is dropped, so a stage subfolder is not a place of its
    own.
    """
    declared = parameters.get("ignored_subfolder_theme_kinds")
    ignored = list(_SUBFOLDER_KINDS_IGNORED) if declared is None else sorted(_strings(declared))
    reading = {"component_kinds": parameters["component_kinds"], "ignored_theme_kinds": ignored}
    between: list[list[list[str]]] = []
    for folder in getattr(place, "between", ()):
        counted = _counted_components(folder, reading)
        named = sorted(
            {
                (component.component_type, component.normalized_value)
                for component in (*counted.components, *counted.unexamined)
            }
        )
        if named:
            between.append([[kind, value] for kind, value in named])
    return between


def _entry_of(subject: object) -> Entry | None:
    """The entry a subject's records are about, where the subject carries one."""
    entry = getattr(subject, "entry", None)
    return entry if isinstance(entry, Entry) else None


def _records_of(subject: object) -> tuple[object, ...]:
    """Return what a subject holds: a sequence of records, or one entry's components and bindings.

    One entry's records are read by what they carry rather than by their type, so the
    Rulebook's predicates need nothing from the inference pass that hands them over.
    """
    if isinstance(subject, (list, tuple)):
        return tuple(subject)
    return (*getattr(subject, "components", ()), *getattr(subject, "bindings", ()))


def _named_components(subject: object, parameters: Mapping[str, object]) -> tuple[Component, ...]:
    """Return the components of the stated kinds read off a name, in canonical order."""
    kinds = _strings(parameters["component_kinds"])
    return tuple(
        sorted(
            (
                item
                for item in _records_of(subject)
                if isinstance(item, Component)
                and item.component_type in kinds
                and item.source_scope is SourceScope.NAME
            ),
            key=lambda component: component.component_id,
        )
    )


#: The component type an indexed atom writes. One spelling, because the filter and the
#: docstring that explains it must not drift apart.
_INDEXED = "indexed"


@dataclass(frozen=True)
class _Counted:
    """What a name's key is built from, once a rule's filters have read the bindings."""

    components: tuple[Component, ...]
    #: The bindings that let a word count, under ``counted_theme_kinds``.
    bindings: tuple[DictionaryBinding, ...] = ()
    #: Words a filter needed to read and the binder never examined.
    unexamined: tuple[Component, ...] = ()
    #: Whether a filter left out a component of the stated kinds.
    filtered: bool = False


def _counted_components(subject: object, parameters: Mapping[str, object]) -> _Counted:
    """Return the components a key counts, in canonical order, and the bindings that decided it.

    A quantity counts by its normalized magnitude whatever the Dictionary made of it. A word
    is read by both filters. An index is read by ``ignored_theme_kinds`` alone, through what its
    label bound: an index that a rule ignores is a distinction it does not want, and an index that
    a rule counts is one whose position it does want.
    """
    named = _named_components(subject, parameters)
    ignored = _optional_strings(parameters, "ignored_theme_kinds")
    counted = _optional_strings(parameters, "counted_theme_kinds")
    if not ignored and not counted:
        return _Counted(components=named)
    bound: dict[str, list[DictionaryBinding]] = defaultdict(list)
    for item in _records_of(subject):
        if isinstance(item, DictionaryBinding):
            bound[item.component_id].append(item)
    kept: list[Component] = []
    deciding: list[DictionaryBinding] = []
    unexamined: list[Component] = []
    for component in named:
        indexed = component.component_type == _INDEXED
        if component.component_type != "word" and not (indexed and ignored):
            kept.append(component)
            continue
        bindings = bound.get(component.component_id)
        if not bindings:
            unexamined.append(component)
            continue
        settled = [
            binding
            for binding in bindings
            if binding.resolution_status is ResolutionStatus.RESOLVED
        ]
        if any(binding.theme_kind in ignored for binding in settled):
            continue
        if counted and not indexed:
            counting = [binding for binding in settled if binding.theme_kind in counted]
            if not counting:
                continue
            deciding.extend(counting)
        kept.append(component)
    return _Counted(
        components=tuple(kept),
        bindings=tuple(sorted(deciding, key=lambda binding: binding.binding_id)),
        unexamined=tuple(unexamined),
        filtered=len(kept) + len(unexamined) < len(named),
    )


def _positions(
    subject: object, kept: Sequence[Component], kinds: frozenset[str]
) -> list[list[str]] | None:
    """Return the kept components of these kinds in the order the name wrote them, or None.

    None where the run recorded no layout for this name, which is a fact about a setting
    (``dictionary.record_name_shapes``) rather than about the name, and is reported as
    unexamined rather than keyed as though the name had no positions.
    """
    slots = getattr(subject, "slots", ())
    if not slots:
        return None
    by_id = {component.component_id: component for component in kept}
    return [
        [by_id[identifier].component_type, by_id[identifier].normalized_value]
        for identifier in slots
        if identifier in by_id and by_id[identifier].component_type in kinds
    ]


def _composition(subject: object) -> tuple[list[tuple[str, str]], list[tuple[str, int]]]:
    """Return a folder's composition digests and child counts, each with its observation."""
    digests: list[tuple[str, str]] = []
    children: list[tuple[str, int]] = []
    if not isinstance(subject, (list, tuple)):
        return digests, children
    for item in subject:
        if not isinstance(item, Observation):
            continue
        if item.key == _COMPOSITION and item.value_text:
            digests.append((item.observation_id, item.value_text))
        elif item.key == _CHILD_COUNT and item.value_integer is not None:
            children.append((item.observation_id, item.value_integer))
    return sorted(digests), sorted(children)


@declare(
    "same_composition",
    version="1",
    summary="Group folders by the shape composition of their direct children",
    effect=RuleEffect.GROUPS,
    reads=Reads.OBSERVATIONS,
    parameters=("minimum_children",),
)
def _build_same_composition() -> Predicate:
    return SameComposition()


@declare(
    "same_content_digest",
    version="3",
    summary="Group entries whose named content digest agrees, over the scope it read",
    effect=RuleEffect.GROUPS,
    reads=Reads.ENTRY_OBSERVATIONS,
    parameters=("digest_algorithm",),
)
def _build_same_content_digest() -> Predicate:
    return SameContentDigest()


@declare(
    "same_observed_metadata",
    version="1",
    summary="Group entries agreeing on every observed filesystem fact",
    effect=RuleEffect.GROUPS,
    reads=Reads.ENTRY_IN_ROOT,
    parameters=("compared_facts",),
)
def _build_same_observed_metadata() -> Predicate:
    return SameObservedMetadata()


@declare(
    "same_component_values",
    version="6",
    summary="Group entries whose names agree on every counted component of a stated kind",
    effect=RuleEffect.GROUPS,
    reads=Reads.RECORDS,
    parameters=(
        "compared_within",
        "component_kinds",
        "counted_theme_kinds",
        "ignored_subfolder_theme_kinds",
        "ignored_theme_kinds",
        "minimum_components",
        "ordered_component_kinds",
    ),
)
def _build_same_component_values() -> Predicate:
    return SameComponentValues()


@declare(
    "named_for_an_acquisition",
    version="1",
    summary="Group files by the acquisition each is named for, the acquisition among them",
    effect=RuleEffect.GROUPS,
    reads=Reads.NAMED_ACQUISITION,
    parameters=(
        "annotation_senses",
        "image_formats",
        "minimum_key_characters",
        "not_made_from_senses",
        "whole_name_levels_up",
    ),
)
def _build_named_for_an_acquisition() -> Predicate:
    return NamedForAnAcquisition()


@declare(
    "named_for_a_series",
    version="1",
    summary="Group a series of numbered files with the table named for it in their folder",
    effect=RuleEffect.GROUPS,
    reads=Reads.NAMED_ACQUISITION,
    parameters=("minimum_series_members", "summary_senses", "table_formats"),
)
def _build_named_for_a_series() -> Predicate:
    return NamedBeside(
        "named_for_a_series",
        "named_series",
        "the file is no numbered member of a series beside a table named for it with a word "
        "saying it holds results, nor that table",
    )


@declare(
    "one_name_in_sibling_folders",
    version="1",
    summary="Group files of one whole name in sibling folders",
    effect=RuleEffect.GROUPS,
    reads=Reads.NAMED_ACQUISITION,
    parameters=(
        "apart_folder_senses",
        "ignored_roles",
        "maximum_folders",
        "product_folder_senses",
        "source_folder_senses",
    ),
)
def _build_one_name_in_sibling_folders() -> Predicate:
    return NamedBeside(
        "one_name_in_sibling_folders",
        "named_beside",
        "no folder beside the file's holds a file of its whole name, or more than the rule's "
        "number of them do",
    )


@declare(
    "same_extracted_shape",
    version="2",
    summary="Group files whose stored column names or array shape agree",
    effect=RuleEffect.GROUPS,
    reads=Reads.OBSERVATIONS,
    parameters=("minimum_columns", "minimum_dimensions"),
)
def _build_same_extracted_shape() -> Predicate:
    return SameExtractedShape()
