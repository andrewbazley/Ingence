"""Predicates that raise something for a person's attention without concluding.

A flagging rule concludes nothing. It says "look at this", which is a different speech act from
"this is that". Keeping the two apart stops a review queue filling with assertions that nobody
made.

The flags divide into two kinds, and both are here on purpose. Some raise a fact about the data:
a name that repudiates its own contents, or a group missing a step that somebody expects. Others
raise a fact about the engine: a folder where extraction fails far more often than the pool
average, or a folder whose vocabulary the Dictionary cannot read. The second kind is the one that
gets left out of systems like this. It keeps the first kind honest, because a rule starved of
inputs and a rule that is wrong look identical in any report that does not separate them.

Nothing here ranks. Salience is its own record and its own job. Severity as an inverse of
confidence is actively wrong. A conflict between two maximally confident claims is the most
alarming thing the engine can produce, so inverting confidence reverses the ordering exactly where
it matters most.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json, dictionary_term_id
from ingence.core.links import is_network_share
from ingence.core.schema import (
    Claim,
    Component,
    DictionaryBinding,
    Entry,
    EntryType,
    FailureClass,
    Observation,
    RuleEffect,
    ScanError,
    SymlinkState,
)
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import DOMAIN_TIE_CONSTRAINT, INDEX_LABEL_CONSTRAINT_OF
from ingence.dictionary.normalize import split_indexed
from ingence.dictionary.programs import FolderView, ProgramLayout, layouts_from
from ingence.infer.subjects import EntryAmongSiblings, sibling_key, template_key
from ingence.record.workflow import LogicalContext, logical_context_of
from ingence.rulebook.predicates.notes import is_a_note
from ingence.rulebook.registry import (
    Abstention,
    Predicate,
    PredicateNotEvaluableError,
    Raised,
    Reads,
    VocabularyRead,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "AuthoredNameDoubt",
    "CopiedInBulk",
    "ExtensionContradictsContent",
    "FailureConcentratedInScope",
    "GroupBelowExpectedSize",
    "GroupIncompleteAgainstWorkflow",
    "IndexedLabelMayMarkReplicates",
    "InstalledSoftwareInScope",
    "ReadingsConflict",
    "StageDisagreesWithADerivation",
    "StatedCountMayMarkReplicates",
    "UnverifiedSubstitutionCandidate",
    "VocabularyUnreadInScope",
]


class AuthoredNameDoubt:
    """A name carrying a doubt term is raised, and nothing is concluded from it.

    ``final_final``, ``use_this_one`` and ``do_not_use`` are things a person typed, and no machine
    emits them. A hit in a name is by construction evidence that a human wrote it. The engine
    cannot say what the doubt means, which is exactly why this predicate flags and does not
    classify. "This folder's name says somebody was unsure" is worth surfacing. "This folder is
    superseded" is a conclusion nobody is entitled to.

    One predicate serves both doubt and repudiation, because the lexicon already grades them.
    ``final_final`` is somebody losing count of their versions, and ``do_not_use`` is somebody
    telling you the contents are wrong. Those are different speech acts that warrant different
    treatment. Which terms hit separates them, and how the hits are read does not. So the two rules
    name different terms and share this mechanism. The alternative, two predicates that read one
    lexicon, would be two places for the reading to drift apart.

    A shipped term may be ordinary vocabulary somewhere. A "control" is not doubtful, a file about
    a "test" statistic is not a test file, and "sample" is a real experimental unit. The section
    marks those, and a rule that wants only the unambiguous signal names the terms it trusts.

    The predicate reads the entry's own name. It raises the entry where the name's own tokens reach
    at least the rule's number of the terms that the rule names, each term counted once however it
    was spelled.

    It counts only the terms that its rule names, and a rule that names none cannot be evaluated.
    No kind says which bindings are doubt. The lexicon files its terms under ``quality``, beside
    filesystem noise and a clinical sense, so counting the kind would raise a folder for being named
    like a cache. ``repudiated_by_name`` names its eight terms and runs. Revision 0's
    ``authored_name_doubt`` names none, and says so until a revision names the terms it means.

    A rule may grade the terms it names. A term in ``low_strength_senses`` is too common in
    ordinary names to raise one alone. Under ``raises: doubt``, the default, it counts only beside
    a named term outside that list. Under ``raises: confirmation``, the rule raises exactly the
    names whose doubt terms are all low-strength, for a person to confirm. Two rules at the two
    settings raise each doubtful name once, and keep apart, by rule, the ones that only a person
    can settle.
    """

    name = "authored_name_doubt"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the named terms the name carries, where they raise it as the rule grades them."""
        counted = _graded(_terms_found(subject, _named_terms(parameters)), parameters)
        if len(counted) < _at_least(parameters, "minimum_terms"):
            return None
        return tuple(sorted(counted))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether a token stood between a named term and another, or too few matched."""
        wanted = _named_terms(parameters)
        free = _free_bindings(subject)
        conflicting = tuple(
            sorted(
                binding.binding_id
                for binding in free
                if binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
                and any(
                    alternative.term_id
                    == dictionary_term_id(
                        term=term, scope=alternative.scope, origin=alternative.origin.value
                    )
                    for alternative in binding.alternatives
                    for term in wanted
                )
            )
        )
        if conflicting:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=conflicting,
                reason="a name token stood between one of the named terms and another sense",
            )
        found = _terms_found(subject, wanted)
        if found:
            inputs = tuple(sorted(binding.binding_id for binding in free if binding.term in found))
            standing = found - _listed(parameters, "low_strength_senses")
            raising = _raising(parameters)
            if raising == _DOUBT and not standing:
                return Abstention(
                    inputs=inputs,
                    reason="the name carries only low-strength terms, which count beside another",
                )
            if raising == _CONFIRMATION and standing:
                return Abstention(
                    inputs=inputs,
                    reason="the name carries a term that stands alone, which a doubt rule raises",
                )
            minimum = _at_least(parameters, "minimum_terms")
            return Abstention(
                inputs=inputs, reason=f"{len(found)} of the named terms, {minimum} asked for"
            )
        return Abstention(reason="the name carries none of the named terms")


class GroupIncompleteAgainstWorkflow:
    """A group is missing a step that the groups like it all have.

    This is the completeness question, and it is a flag and not a claim. Saying a set is
    incomplete asserts that something is absent, and an absence claim is only as good as the
    definition of what should have been there. If the definition is wrong, the engine confidently
    reports missing files that were never part of the work. Users forgive that failure least, and
    it makes them stop trusting everything else.

    So the expected set is declared and never induced: the rule's ``required_stages`` names it.
    The rule does not fold the result into one completeness number weighted against a hygiene
    penalty. A number that combines "two of three stages present" with "this folder's name says
    somebody was unsure" cannot be read back into either fact, and the two call for entirely
    different actions.

    What is missing is named, and the count is not the output. "No quantification step" tells
    somebody where to look. "Sixty per cent complete" tells them nothing they can act on and
    invites a threshold.

    Declared and not evaluable. It reads a group and the stages its members were classified into,
    and no phase hands a flagging predicate a group.
    """

    name = "group_incomplete_against_workflow"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "group_incomplete_against_workflow reads a group's members and the stages "
            "they were classified into, and no phase hands a flagging predicate a group yet"
        )
        raise PredicateNotEvaluableError(message)


class GroupBelowExpectedSize:
    """A group holds fewer members than the work it represents calls for.

    Examples are three biological replicates, two technical repeats, or a plate's worth of wells.
    The expectation is entirely the user's, and the engine holds none of its own. This is why the
    number is a parameter with no defensible default. What counts as enough repetition is a
    question about a field's conventions and sometimes about one reviewer's opinion. An engine that
    asserted it would be making a scientific claim, which is out of scope.

    Below expected is not incomplete, and the two are separate rules. A group may hold every stage
    of the workflow in each of two replicates where three were wanted. Nothing is missing from any
    member. The group only has fewer members. Merging them into one completeness figure loses which
    of the two a person would have to do something about.

    The floor is a prompt and not a gate. Nothing downstream refuses to publish a group under it,
    because a pilot experiment with one replicate is a real thing, and the engine is not entitled
    to an opinion about whether it should exist.

    Declared and not evaluable.
    """

    name = "group_below_expected_size"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "group_below_expected_size counts a group's members, and no phase hands "
            "a flagging predicate a group yet"
        )
        raise PredicateNotEvaluableError(message)


class UnverifiedSubstitutionCandidate:
    """Entries that look identical from outside and have never been compared.

    This flag turns an equivalence into a question worth an afternoon. Metadata agreement groups
    files that are plausibly copies. Only a content digest settles it. Hashing an entire pool is
    expensive, and hashing the members of one small group is not. So the useful output is not a
    verdict. It is a short list of comparisons worth making, ordered by what they would buy.

    The flag exists because the cheap relation must never quietly become the expensive one. A
    constraint refuses an identity edge asserted from metadata, which is the hard half. This flag
    is the soft half. Having refused, the engine says what it would take to answer the question,
    and does not leave a group labeled with a relation that nobody reads.

    An expected repetition is not a duplicate, and the distinction is worth keeping. A pool where
    the same analysis was run three times holds three copies of many outputs by design. An engine
    that reports them as waste is wrong in a way that is annoying and not dangerous, but it is
    wrong often enough to bury the real finding.

    Declared and not evaluable.
    """

    name = "unverified_substitution_candidate"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "unverified_substitution_candidate reads a metadata-equivalence group, "
            "and no phase hands a flagging predicate a group yet"
        )
        raise PredicateNotEvaluableError(message)


@dataclass(frozen=True)
class _LabelRun:
    """One indexed label the Dictionary places nowhere, and where it was found in a scope."""

    #: The components carrying the label, by the index each was written with.
    carried: dict[int, list[Component]] = field(default_factory=lambda: defaultdict(list))
    #: The identifiers of the folders the label was found in. A container member compares
    #: within its container, which is what its parent identifier already names.
    folders: set[str] = field(default_factory=set)
    #: The entries carrying it, so a count of files is exact rather than a count of components.
    entries: set[str] = field(default_factory=set)

    @property
    def run_to(self) -> int:
        """Return n where the indices are exactly 1 to n, or zero where they are not a run."""
        indices = set(self.carried)
        highest = max(indices, default=0)
        return highest if indices == set(range(1, highest + 1)) else 0


class IndexedLabelMayMarkReplicates:
    """A label the Dictionary cannot place, numbered like a replicate set, raised for a person.

    The rule is a flag and never a claim. The engine is not entitled to decide that ``V`` means
    version, variant, volume or replicate. It can see a shape: a short label that it has no sense
    for, numbered from one to a small n, recurring in more than one folder. Every one of those
    readings is live, and only a person knows which. So the rule notices the shape and asks.

    It is worth asking on a real corpus. The mechanism that binds an indexed label to a replicate
    sense works, and on one real collection of laboratory folders it bound nothing at all. Of the
    indexed labels examined, not one reached a replicate sense. That laboratory did not mark
    replicates with any word the Dictionary holds. It did hold labels that the Dictionary places
    nowhere, some of them forming a short run from one over many files. This is exactly the shape
    that a replicate marker has and a version number does not.

    Answering is a Dictionary edit and never a rule edit. A person says ``V`` is a surface of a
    replicate sense at ``USER`` origin, scoped to where they mean it. Every rule that reads
    replicate senses then picks it up, including the filter that leaves a replicate out of an
    experiment's identity. That is the Dictionary doing its job, and it is why this is a flag and
    not a parameter that somebody tunes.

    The Dictionary placing the label nowhere is the condition, which is narrower than binding no
    replicate sense. ``nd2`` binds a vendor format on its whole text. Its label ``nd`` is not a
    candidate, because the Dictionary has an answer for what that text says, and raising it would
    ask a settled question. So a label is a candidate only where every binding on its component
    reached no sense at all. A component that the binder never looked at is not a candidate
    either. Never asked and asked-and-found-nothing are different states.

    The unit of recurrence is the folder, and this is a substitution that is written down. The
    replicate design asks for two or more distinct experiments. An experiment is the class of
    entries in one folder that share a name identity once replicate components are left out. That
    class cannot hold this label's files. The identity can only leave out an index whose label
    binds a replicate sense, which is precisely what this flag exists to discover. So ``expt_V1``
    and ``expt_V2`` sit in two classes, and counting classes would count the replicates and not
    the experiments. The mechanism forces the substitution. The rule's own sentence says folders,
    so that nobody reads it as experiments.

    The rule reads a scope, because none of this is a fact about any one file. A label that recurs
    across folders is a fact about a region. Raising it per file would say it once per file, which
    is one question asked thousands of times.
    """

    name = "indexed_label_may_mark_replicates"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise every unplaced label numbered like a replicate set, citing what carries it."""
        qualifying = _qualifying_labels(subject, parameters)
        if not qualifying:
            return None
        return Raised(
            values=tuple(sorted(qualifying)),
            inputs=tuple(
                sorted(
                    component.component_id
                    for found in qualifying.values()
                    for components in found.carried.values()
                    for component in components
                )
            ),
            reason="; ".join(
                f"{label} runs 1 to {found.run_to} across {len(found.folders)} folders "
                f"and {len(found.entries)} files"
                for label, found in sorted(qualifying.items())
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say which condition the scope's unplaced labels failed, or that it holds none."""
        found = _unplaced_labels(subject)
        if not found:
            return Abstention(reason="no indexed label in this scope is one the Dictionary misses")
        maximum = _at_least(parameters, "maximum_index")
        folders = _at_least(parameters, "minimum_folders")
        runs = {label: run.run_to for label, run in found.items()}
        if not any(runs.values()):
            return Abstention(
                reason=f"{len(found)} unplaced labels, none numbered from 1 without a gap"
            )
        short = [label for label, to in runs.items() if 0 < to <= maximum]
        if not short:
            return Abstention(
                reason=f"every run from 1 reaches past {maximum}, which is a version and not a set"
            )
        return Abstention(
            reason=f"{len(short)} labels run from 1, none in {folders} or more folders"
        )


#: The two readings a stated count has, and the only two. A cardinal is a statement about how many
#: replicates something rests on. An ordinal is a statement about which one this is. They are
#: different facts, and a rule that folded them together would say neither.
_CARDINAL = "cardinal"
_ORDINAL = "ordinal"


def _counts_stated(subject: object, parameters: Mapping[str, object]) -> dict[int, list[str]]:
    """Return each count an entry's name states, under the bound, and the records that say so.

    The marker follows the axis and is not written beside it. The rule names the kind, and the
    restriction marker that a binding on that kind records is read off
    :data:`~ingence.dictionary.discover.INDEX_LABEL_CONSTRAINT_OF`. So a rule cannot name an axis
    whose labels never bind, and cannot name a marker that its axis does not record.

    The function tests the marker and never the slot. A bare number records a name-slot marker too,
    and testing the slot would read every digit in a name as a stated count.
    """
    kind = str(parameters.get("theme_kind", ""))
    marker = INDEX_LABEL_CONSTRAINT_OF.get(kind)
    if marker is None:
        admitted = ", ".join(sorted(INDEX_LABEL_CONSTRAINT_OF))
        message = (
            f"stated_count_may_mark_replicates reads an indexed label's binding, and no label "
            f"binds a sense of kind {kind!r}; the kinds a label may bind are {admitted}"
        )
        raise PredicateNotEvaluableError(message)
    maximum = _at_least(parameters, "maximum_count")
    values = {
        component.component_id: component.normalized_value
        for component in getattr(subject, "components", ())
        if isinstance(component, Component) and component.component_type == _INDEXED_COMPONENT
    }
    found: dict[int, list[str]] = defaultdict(list)
    for binding in getattr(subject, "bindings", ()):
        if not isinstance(binding, DictionaryBinding) or binding.constraint != marker:
            continue
        if binding.resolution_status is not ResolutionStatus.RESOLVED:
            continue
        value = values.get(binding.component_id)
        split = None if value is None else split_indexed(value)
        if split is None or not 1 <= split[1] <= maximum:
            continue
        found[split[1]].append(binding.binding_id)
    return {index: sorted(cited) for index, cited in sorted(found.items())}


def _stage_claims(subject: object, parameters: Mapping[str, object]) -> tuple[Claim, ...]:
    """The claims whose conclusion is read as this file's stage, in canonical order.

    They are named as rule identifiers on the rule, which is how the Inventory's own placement
    reads them. A project revision that renames a stage rule passes its own names. Reading every
    claim whose value happens to spell a rung would make any rule that concludes ``analysis`` a
    stage rule by accident.
    """
    named = _listed(parameters, "stage_rules")
    return tuple(
        sorted(
            (
                claim
                for claim in getattr(subject, "claims", ())
                if isinstance(claim, Claim)
                and claim.rule_id in named
                and claim.resolution_status is ResolutionStatus.RESOLVED
                and claim.object_reference
            ),
            key=lambda claim: claim.claim_id,
        )
    )


def _outranking(subject: object, parameters: Mapping[str, object]) -> tuple[str, str] | None:
    """Return the reading a rule outranking any workflow fixes, or None where none concluded.

    An instrument's own extension is an observation, and a program is a derivation, whatever a
    workflow says (``OUTRANKS_THE_WORKFLOW``). So a file that one of those rules concluded about
    takes its reading from that rule. Both at once is a contradiction between two readings that
    each outrank the workflow, and the uncertain reading is the honest one.
    """
    concluded = [
        claim
        for claim in getattr(subject, "claims", ())
        if isinstance(claim, Claim) and claim.resolution_status is ResolutionStatus.RESOLVED
    ]
    observed = sorted(
        {claim.rule_id for claim in concluded} & _listed(parameters, "observation_rules")
    )
    derived = sorted(
        {claim.rule_id for claim in concluded} & _listed(parameters, "derivation_rules")
    )
    if observed and derived:
        return _ORDINAL, (
            f"{', '.join(observed)} makes it an observation and {', '.join(derived)} a "
            "derivation, and each outranks any workflow"
        )
    if derived:
        return _CARDINAL, f"{', '.join(derived)} concluded it, and a program is a derivation"
    if observed:
        return _ORDINAL, (
            f"{', '.join(observed)} concluded it, and an instrument's own extension is an "
            "observation"
        )
    return None


def _reading_of(subject: object, parameters: Mapping[str, object]) -> tuple[str, str]:
    """Return which reading this file's stage licenses, and the sentence saying why.

    A rule that outranks the workflow decides first. A program is a derivation and an instrument's
    own extension is an observation, whatever a workflow says. Otherwise the reading is a cardinal
    only where every workflow in force puts the file past the rung where data enters, because that
    is the one case in which the string is not ambiguous. An analysis that says ``N=3`` is saying
    what it rests on. Everywhere else, the reading is the ordinal one and it is raised
    uncertainly, because the same string means both things and nothing in it says which. That
    covers the observation rung, before it, unplaced files, no stage claimed, no workflow declared,
    and two workflows that disagree.
    """
    fixed = _outranking(subject, parameters)
    if fixed is not None:
        return fixed
    workflows = tuple(getattr(subject, "workflows", ()))
    if not workflows:
        return _ORDINAL, "no workflow is declared, so nothing says where data enters"
    stages = [
        claim.object_reference
        for claim in _stage_claims(subject, parameters)
        if claim.object_reference
    ]
    if not stages:
        return _ORDINAL, "no rule placed this file at a stage"
    placed = {logical_context_of(stage, workflow) for stage in stages for workflow in workflows}
    named = ", ".join(sorted(stages))
    if placed == {LogicalContext.DERIVATION}:
        return _CARDINAL, f"its stage {named} is past the rung where data enters"
    if len(placed) > 1:
        return _ORDINAL, f"its stage {named} is placed two ways by the workflows in force"
    return _ORDINAL, f"its stage {named} is at or before the rung where data enters"


class StatedCountMayMarkReplicates:
    """A name stating a count, raised as what it rests on or as which one this is.

    ``N=3`` is a count, and which count depends on where the file sits. On a derivation, such as an
    analysis, a summary or a pooled result, it is a cardinal: this rests on three replicates. On an
    observation it is an ordinal: this is the third. On an observation, it is more obvious that
    someone is labeling the Nth of their replicate experiments while actually collecting the data.

    The flag decides nothing about the file that carries it. A file with ``N=3`` in its name is
    handled like an individual file, all of whose traits may be uncertain. What the count does is
    raise the expectation of siblings, as in "there are probably other experiments like this". A
    later grouping or proposing rule reads that expectation. So this predicate flags and never
    claims. The pooling claim rests on a ladder of which a stated count is the weakest rung, and a
    stated count never licenses it alone.

    The ordinal reading is raised uncertainly, and this is a requirement and not caution. People
    mix up the cardinal and the ordinal, and others will too. A person who writes ``N=3`` on a raw
    file meaning "three exist" and a person who means "this is the third" produce the same
    string, and nothing in it says which. Where the engine cannot tell, it asks.

    The context is read here, and it could not be read where the stage is concluded. A classifying
    rule cannot read another classifying rule's claim in the same pass. A flagging rule can,
    because ``EntryRecords.claims`` carries every claim that the earlier phases published. That
    keeps classification a single round.

    The bound is on the rule and not on the tokenizer. A large ``N`` is a true statement of a count
    and a false statement of a replicate design, and only a reader tells those apart. So the
    normalizer joins every ``label=number`` it meets, and this rule refuses the ones that are too
    large to be a replicate set.
    """

    name = "stated_count_may_mark_replicates"
    #: 2 reads the rules outranking any workflow before the workflow.
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise every count the name states, each with the reading its stage licenses."""
        found = _counts_stated(subject, parameters)
        if not found:
            return None
        reading, because = _reading_of(subject, parameters)
        return Raised(
            values=tuple(
                canonical_json({"count": index, "reading": reading}) for index in sorted(found)
            ),
            inputs=tuple(sorted({cited for bindings in found.values() for cited in bindings})),
            reason="; ".join(
                (
                    f"the name states a count of {index}, read as "
                    + (
                        f"a cardinal -- this rests on {index} replicates"
                        if reading == _CARDINAL
                        else f"an ordinal -- this may be replicate {index} of a set, "
                        "and the engine cannot tell which reading was meant"
                    )
                    + f", because {because}"
                )
                for index in sorted(found)
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the name states no count, or states one past the bound."""
        kind = str(parameters.get("theme_kind", ""))
        marker = INDEX_LABEL_CONSTRAINT_OF.get(kind, "")
        stated = [
            binding
            for binding in getattr(subject, "bindings", ())
            if isinstance(binding, DictionaryBinding)
            and binding.constraint == marker
            and binding.resolution_status is ResolutionStatus.RESOLVED
        ]
        if not stated:
            return Abstention(reason=f"no indexed label in the name binds a sense of kind {kind}")
        maximum = _at_least(parameters, "maximum_count")
        return Abstention(
            reason=(
                f"{len(stated)} stated counts, every one of them past {maximum}, which is a "
                f"true count and not a replicate design"
            )
        )


class ContentHoldsAPhrase:
    """A file whose own text holds one of the phrases a rule names, recorded and never obeyed.

    The engine does not engineer any hard file skips. A file may contain a "DO NOT ANALYZE" note
    or something similar, which is often a note from and to the researcher. The engine records the
    note and assumes that Ingence has full read access to every file in the user-specified
    directory.

    So this predicate records the note as a finding. Nothing reads it as an instruction. No
    setting, no rule and no pass skips a file because of what its content says, and the engine has
    no place where such a skip could be configured. A person who reads the queue decides what to do
    about a note somebody left, and only a person can decide it.

    The predicate reads what a byte scan found and never opens a file itself. The scan is opt-in
    (``extraction.byte_scan_sections``). It searches for the surfaces of the Dictionary sections
    that a project names, and it records each hit as an observation. Where no scan ran, there is
    nothing to read, and the rule says so. The rule names the phrases, so a laboratory that writes
    the note differently edits a sentence and not the engine.
    """

    name = "content_holds_a_phrase"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the named phrases this file's own text holds, or None where it holds none."""
        found = _phrases_found(subject, parameters)
        if not found:
            return None
        named = ", ".join(sorted(found))
        return Raised(
            values=tuple(sorted(found)),
            inputs=tuple(
                sorted(identifier for identifiers in found.values() for identifier in identifiers)
            ),
            reason=(
                f"its own text holds {named}, which is a note somebody left about this file and "
                f"is recorded as one; nothing here skips a file because of what it says"
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether no scan ran, or it ran and found none of the phrases."""
        observations = _observations(subject)
        searched = [
            observation
            for observation in observations
            if observation.key == _TERMS_SEARCHED and (observation.value_integer or 0) > 0
        ]
        if not searched:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason=(
                    "no byte scan has read this file's text, which is opt-in per project "
                    "(extraction.byte_scan_sections), so nothing about what it says is known"
                ),
            )
        return Abstention(
            inputs=tuple(observation.observation_id for observation in searched),
            reason="its text was searched and holds none of the phrases this rule names",
        )


#: What the byte scan records: one observation per term found, and one saying how many it looked
#: for (``extractors/generic.py``).
_TERM_FOUND = "scan.term."
_TERMS_SEARCHED = "scan.terms_searched"


def _phrases_found(subject: object, parameters: Mapping[str, object]) -> dict[str, list[str]]:
    """Return, per named phrase a byte scan found in this file, the observations that found it."""
    wanted = _declared_strings(parameters, "phrases")
    if not wanted:
        message = (
            "content_holds_a_phrase raises the phrases its rule names, and this rule names none"
        )
        raise PredicateNotEvaluableError(message)
    found: dict[str, list[str]] = {}
    for observation in _observations(subject):
        if not observation.key.startswith(_TERM_FOUND):
            continue
        if (observation.value_integer or 0) <= 0:
            continue
        # ``scan.term.<phrase>.<encoding>``: the phrase is what lies between.
        phrase = observation.key[len(_TERM_FOUND) :].rsplit(".", 1)[0]
        if phrase in wanted:
            found.setdefault(phrase, []).append(observation.observation_id)
    return found


class ContentUnreadable:
    """A file whose content cannot be read, so it is read from its name and its place alone.

    The predicate replaces ``empty_and_untouched``, which read an empty, old file as a stub for
    something held elsewhere. On one real folder, all 34 files that rule raised stood for nothing.
    They were a program's temporary files, empty logs, an ``__init__`` and a Qt marker. So an empty
    file is read as an empty file. It stays in the analysis, flagged, because it cannot be scanned
    deeply or hashed. The analysis goes on without that reading. A file in cold storage is old, and
    old is not irrelevant.

    The predicate raises the fact and claims nothing about what the file stands for. The value says
    in which way the content is out of reach, because each way is a different situation:

    - ``empty``: the file holds no bytes, and none of the cases below applies;
    - ``empty_output``: the file is empty, and a file with bytes sits in its own folder or in a
      folder beside it with the same name once the numbers and the extension are set aside. A
      file with bytes in its folder that has the same extension and the same first and last name
      parts also counts (:func:`~ingence.infer.subjects.template_key`). The file is a real output
      of a run that found nothing;
    - ``empty_and_old``: the file is empty, was last written before the date ``written_before``
      names, and nothing beside it says more. Version 2 called this ``dehydrated_or_archived``;
    - ``link_without_a_target``: a symlink whose target is not there. A link that the engine could
      not ask about is not raised;
    - ``archive_link``: a link whose text names another server's share (``\\??\\UNC\\``). An
      archiving system leaves such a link when it moves a file to another server;
    - ``link_outside_the_root``: any other symlink whose target is a path outside the root it was
      found under;
    - ``not_a_regular_file``: a socket, a pipe or a device, which has no content to read.

    The census reads a link's target from the link's text and never ``stat``-s it.

    The extension is set aside with the numbers because a tracking tool can write each movie's
    results as text and as a table. Where it finds nothing, the table is empty beside text that
    holds bytes. On one laboratory's folder, 50 of 81 empty files were such tables. A batch run
    can also write an empty table for each brightfield movie, with a name that differs from the
    tables that hold data by a word and not a number. The template key covers that case.

    With ``notes_as_names`` on, a note written as a file name is not raised. The predicate reads
    what a note is through this rule's own ``minimum_words`` and ``note_extensions``. Each rule
    that leaves a note out holds its own copy of these parameters, so a person tunes them
    together with the note rule's.

    Nothing about archives is inferred and nothing is excluded. The file keeps its name, its
    position, its family and its identity by name. What it cannot have is a digest or a deep
    reading, and the dive says so per entry and does not leave a silent gap.

    The predicate runs on a census alone, as the rule it replaces did. Every fact it reads is one
    that the filesystem gave up without the file being opened.
    """

    name = "content_unreadable"
    version = "7"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return each way this entry's content is out of reach, or None where it is not."""
        entry, beside, nearby = _among_siblings(subject)
        if entry is None:
            return None
        raised = self._raised(entry, parameters, beside | nearby)
        if not raised:
            return None
        return Raised(
            values=tuple(sorted(raised)),
            inputs=(entry.entry_id,),
            reason=(
                "its content cannot be read, so it is interpreted from its name and its "
                "position alone: no digest, no deep reading, and nothing inferred about what "
                "it may stand for"
                + (
                    "; a file of the same name but for its numbers holds bytes beside it"
                    if _EMPTY_OUTPUT in raised
                    else ""
                )
            ),
        )

    def _raised(
        self, entry: Entry, parameters: Mapping[str, object], siblings: frozenset[str]
    ) -> set[str]:
        """Which ways this entry's content is out of reach, by the facts a census records."""
        raised: set[str] = set()
        if entry.entry_type is EntryType.OTHER:
            raised.add(_NOT_A_REGULAR_FILE)
        if entry.symlink_state is SymlinkState.BROKEN:
            # And nothing else, because a dangling link's size is not a fact about content. On
            # POSIX a symlink's own size is the length of the path it holds. On Windows a census
            # reads zero, so the same link raised `empty` there and not here. A platform property
            # is measured and never assumed. The link's content is out of reach because its target
            # is gone, which is one way and not two.
            return {_LINK_WITHOUT_A_TARGET}
        if entry.symlink_state is SymlinkState.OUTSIDE_ROOT:
            # The same reasoning: a link's own size is the length of its text, and its content
            # is out of reach one way, because it is held outside the pool. A link to another
            # server's share is the archive's, read from its text alone.
            if entry.symlink_target and is_network_share(entry.symlink_target):
                return {_ARCHIVE_LINK}
            return {_LINK_OUTSIDE_THE_ROOT}
        if entry.entry_type in (EntryType.FILE, EntryType.SYMLINK) and entry.size_bytes == 0:
            if parameters.get("notes_as_names") is True and is_a_note(entry, parameters):
                # A note written as a file name: its content is its name, by the thresholds this
                # rule declares (version 7).
                return raised
            before = _instant(parameters, "written_before")
            touched = entry.modified_ns
            if empty_among_data(entry, siblings):
                raised.add(_EMPTY_OUTPUT)
            elif before is not None and touched is not None and touched < before:
                raised.add(_EMPTY_AND_OLD)
            else:
                raised.add(_EMPTY)
        return raised

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that this file's content is readable as far as the filesystem can tell."""
        subject, _, _ = _among_siblings(subject)
        if subject is None:
            return Abstention(reason="handed something other than an entry")
        if subject.entry_type is EntryType.DIRECTORY:
            return Abstention(reason="a folder has no content of its own to read")
        if subject.size_bytes is None:
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason="the filesystem reported no size, so whether it holds bytes is unknown",
            )
        return Abstention(
            inputs=(subject.entry_id,),
            reason="it holds bytes and its content is there to be read",
        )


#: The ways a file's content can be out of reach, each its own question, named so a person reads
#: them without a glossary.
_EMPTY = "empty"
_EMPTY_OUTPUT = "empty_output"
_EMPTY_AND_OLD = "empty_and_old"
_ARCHIVE_LINK = "archive_link"
_LINK_WITHOUT_A_TARGET = "link_without_a_target"
_LINK_OUTSIDE_THE_ROOT = "link_outside_the_root"
_NOT_A_REGULAR_FILE = "not_a_regular_file"


def _among_siblings(subject: object) -> tuple[Entry | None, frozenset[str], frozenset[str]]:
    """Read an entry and what its neighbors hold, from the shape the rule declared.

    An entry handed alone, by a caller that builds one by hand or by a pass with no proximity to
    give, is read with no neighbors, which is the version 2 reading of it. The keys returned are
    the sibling keys of the folder and the folders beside it, and the batch templates of the
    folder. Each is marked so that the two kinds of key cannot meet.
    """
    if isinstance(subject, EntryAmongSiblings):
        batches = frozenset(f"template:{key}" for key in subject.templates)
        return subject.entry, subject.beside | batches, subject.nearby
    if isinstance(subject, Entry):
        return subject, frozenset(), frozenset()
    return None, frozenset(), frozenset()


def empty_among_data(entry: Entry, siblings: frozenset[str]) -> bool:
    """Whether an empty file sits among siblings holding data: its name but for its numbers, or
    its batch's template, is held by a file with bytes nearby."""
    if entry.entry_type not in (EntryType.FILE, EntryType.SYMLINK) or entry.size_bytes != 0:
        return False
    template = template_key(entry.name)
    return sibling_key(entry.name) in siblings or bool(
        template and f"template:{template}" in siblings
    )


class ReadingsConflict:
    """Two readings of one thing disagree, and neither is preferred.

    Examples are a document clock and a filename clock that name different days, two extractors
    that return different values at one tier, and a token that the Dictionary holds twice with
    nothing to separate the senses. In every case the engine has already done the right thing by
    abstaining, and abstention is invisible. A null in a column looks exactly like a question
    nobody asked. This rule makes the abstention visible, which is the difference between a system
    that is honest and one that appears not to have noticed.

    A conflict is among the most informative outputs the engine has. Two confident readings that
    disagree are a fact about the data that nothing else would have surfaced. A file named in March
    whose document was made in January is a real event that somebody can explain, and it is only
    visible because both readings were kept.

    The rule raises the conflict and does not resolve it, and no tie-break is offered. Picking one
    reading would make the answer depend on registry order. It would also replace a true statement
    about disagreement with a false one about agreement.

    The rule is evaluable for a Dictionary reading and not for a clock. A binding that stands
    between senses names every contender, so a name token that the Dictionary holds more than once
    is raised with the binding that could not choose. Two readings of a clock at one tier are
    stored and not compared. So a rule that reads ``event_time`` is not evaluable and says why.
    The two readings are kept in separate rules so that neither fires on the other's fact.
    """

    name = "readings_conflict"
    #: 3 leaves out a tie between domains the folder's files could not decide, in fewer files
    #: than ask a question (:func:`_conflicting`). 2 lists the reason's clauses in the order of
    #: their own text. Version 1 listed them in binding-identifier order, and identifiers derive
    #: from the root, so one name under two roots read its reason in two orders. Findings differed
    #: between two trees for no reason but that.
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the name tokens whose binding stands between enough senses, citing each binding."""
        _refuse_all_but_the_dictionary(parameters)
        tied = [
            binding
            for binding in _conflicting(subject)
            if len(binding.alternatives) >= _at_least(parameters, "minimum_alternatives")
        ]
        if not tied:
            return None
        tokens = {
            component.component_id: component.raw_value or component.normalized_value
            for component in getattr(subject, "components", ())
        }
        described = [
            f"{tokens.get(binding.component_id, binding.component_id)} stands between "
            f"{len(binding.alternatives)} senses of kind "
            f"{', '.join(sorted({str(item.theme_kind) for item in binding.alternatives}))}"
            for binding in tied
        ]
        return Raised(
            values=tuple(
                sorted({tokens.get(binding.component_id, binding.component_id) for binding in tied})
            ),
            inputs=tuple(sorted(binding.binding_id for binding in tied)),
            reason="; ".join(sorted(described)),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether a tie stood between too few senses, or none stood at all."""
        minimum = _at_least(parameters, "minimum_alternatives")
        short = sorted(binding.binding_id for binding in _conflicting(subject))
        if short:
            return Abstention(
                inputs=tuple(short),
                reason=f"a binding stood between fewer than {minimum} senses",
            )
        return Abstention(reason="no binding on the name stands between senses")


class FailureConcentratedInScope:
    """A scope where the engine fails far more often than it does elsewhere.

    This rule keeps every other rule honest, and it is about the engine and not about the data.
    Failures are classified by who can fix them. Access is the environment, comprehension is
    coverage, and interpretation is the rules. A rule that fires badly in a folder whose failures
    are almost all comprehension is not a rule that needs loosening. It is a rule that is starved
    of inputs, and loosening it would compensate for missing data by degrading something that was
    correct. That is the commonest way that rule tuning makes a system worse.

    The rule measures concentration and not a count, and that is the whole design. A hundred
    failures in a pool of a hundred thousand files is background. A hundred in a folder of a
    hundred and ten is a mount that is not mounted, or a format that nothing has been written for,
    and it is one finding and not a hundred. Comparing a scope against the pool is what turns a
    long list into a short one.

    The remedy is named because the class names it. Access sends somebody to the permissions and
    never to a rule. Comprehension sends somebody to an extractor or the Dictionary. Reporting the
    class alongside the rate makes the flag actionable and not merely alarming.

    The rule counts entries and not failures. Its own sentence counts "its entries failing", so a
    file that failed three times is one failing file. Counting failures would let one pathological
    file outrank a folder that nobody can read.

    Both rates are computed here, from the same records, by the same code. The scope carries the
    pool it is part of (``ScopeRecords.pool``) and not a total that somebody else aggregated. So
    there is one definition of failing applied to two populations and no second copy to drift.

    Interpretation is refused and the other two classes are not. Access and comprehension are the
    scan layer's, recorded on a :class:`~ingence.core.schema.ScanError` before the pass begins. An
    interpretation failure is a rule's own, recorded while this very pass runs. A flag that read
    one would be reading a record that the phase it sits in is still producing. This is a refusal
    and not a silence.

    What the rule cannot see is stated. A path that could not be stat'd has no row in ``entries``,
    so it is in no folder and raises nobody's rate. A mount that is not mounted at all enumerates
    nothing.

    A folder that could not be entered is raised where it is listed, whatever the floor (version
    2). Its contents were never listed, so they are not entries, and no folder's rate can count
    them. Counted as one failing entry in its parent, such a folder stays under a floor of five.
    So the flag whose rationale is exactly "a permission nobody granted" could never fire. The
    failure of such a folder is total, whatever it held, so the scope that lists it is raised and
    names it.
    """

    name = "failure_concentrated_in_scope"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the scope failing far more often than the pool, and name the class."""
        wanted = _failure_class(parameters)
        unentered = _unentered(getattr(subject, "entries", ()), wanted)
        if unentered:
            return Raised(
                values=(wanted.value,),
                inputs=unentered,
                reason=(
                    f"{len(unentered)} folder{'s' if len(unentered) != 1 else ''} here could not "
                    f"be entered ({wanted.value}), so nothing beneath "
                    f"{'them' if len(unentered) != 1 else 'it'} was seen"
                ),
            )
        here = _failing(getattr(subject, "entries", ()), wanted)
        # The floor first, and the pool only after it. The pool is the same population for
        # every scope and reading it per scope is quadratic in the pool's own size. On a real
        # tree that cost most of a pass's time, for an answer almost every scope had already
        # been refused on.
        if here.failing < _at_least(parameters, "minimum_failures"):
            return None
        pool = _failing(getattr(subject, "pool", ()), wanted)
        factor = _concentration(parameters)
        if not pool.rate or here.rate < pool.rate * factor:
            return None
        return Raised(
            values=(wanted.value,),
            inputs=_failing_entries(getattr(subject, "entries", ()), wanted),
            reason=(
                f"{here.failing} of {here.total} entries failed on {wanted.value}, a rate of "
                f"{here.rate:.2f} against the pool's {pool.rate:.2f}"
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the region failed too little, or no more often than the pool."""
        wanted = _failure_class(parameters)
        here = _failing(getattr(subject, "entries", ()), wanted)
        floor = _at_least(parameters, "minimum_failures")
        if here.failing < floor:
            # The abstention every scope but a handful reaches, and the one that must not read
            # the pool to say so: a predicate abstains once per scope and this is where a
            # quadratic read would hide.
            return Abstention(
                reason=(
                    f"{here.failing} of {here.total} entries failed on {wanted.value}, fewer "
                    f"than the {floor} a rate needs to mean anything"
                )
            )
        pool = _failing(getattr(subject, "pool", ()), wanted)
        if not pool.rate:
            return Abstention(
                reason=f"nothing in the pool failed on {wanted.value}, so there is no rate to "
                f"exceed"
            )
        return Abstention(
            reason=(
                f"{here.failing} of {here.total} entries failed on {wanted.value}, a rate of "
                f"{here.rate:.2f}, under {_concentration(parameters):.1f} times the pool's "
                f"{pool.rate:.2f}"
            )
        )


#: What an unbound share is computed over. A word is a word somebody chose. Every kind also
#: counts dates, numbers, quantities, versions and indexed atoms, which the normalizer
#: understands structurally while they intentionally reach no sense.
_WORD_OCCURRENCES = "word_occurrences"
_ALL_KINDS = "all_kinds"

#: The component kind neither denominator counts. The extension is its own slot, looked up
#: against formats alone, so a folder whose extensions the Dictionary has no format for
#: is a fact about detection's coverage and not about anybody's vocabulary.
_EXTENSION_COMPONENT = "extension"

#: The component type a word writes. One spelling, beside the indexed atom's.
_WORD_COMPONENT = "word"

#: A component's whole occurrence count, because a term appearing three times in one name is
#: a different fact from a term appearing once and the record keeps the number.
_ONE_OCCURRENCE = 1


def _share(parameters: Mapping[str, object], key: str) -> float:
    """Read a share a rule declares, as a fraction between nothing and everything."""
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    return min(1.0, max(0.0, float(value)))


def _unread_bindings(subject: object, parameters: Mapping[str, object]) -> tuple[str, ...]:
    """The bindings that found nothing, in canonical order, so a finding walks back to them.

    The records are the ones that the scope's own entries carry, which is all a finding about a
    region may cite.
    """
    denominator = _counted_kinds(parameters)
    counted: set[str] = set()
    for records in getattr(subject, "entries", ()):
        kinds = {
            component.component_id: component.component_type
            for component in getattr(records, "components", ())
            if isinstance(component, Component)
        }
        for binding in getattr(records, "bindings", ()):
            if not isinstance(binding, DictionaryBinding):
                continue
            if binding.resolution_status is not ResolutionStatus.EXAMINED_NO_EVIDENCE:
                continue
            kind = kinds.get(binding.component_id)
            if kind is None or kind == _EXTENSION_COMPONENT:
                continue
            if denominator == _WORD_OCCURRENCES and kind != _WORD_COMPONENT:
                continue
            counted.add(binding.binding_id)
    return tuple(sorted(counted))


@dataclass(frozen=True, slots=True)
class _Legibility:
    """How much of a region's vocabulary the Dictionary could read, in occurrences."""

    #: Occurrences the binder looked at and reached a sense for, a conflict included: a
    #: binding standing between two senses is the Dictionary having an answer, not lacking one.
    read: int = 0
    #: Occurrences the binder looked at and found nothing for.
    unread: int = 0
    #: Occurrences the binder never looked at. Counted apart and never as unread, because
    #: never-asked and asked-and-found-nothing are different states, and folding them
    #: together would report a project with binding switched off as illegible.
    unexamined: int = 0

    @property
    def examined(self) -> int:
        """The denominator: occurrences the binder actually had an opinion about."""
        return self.read + self.unread

    @property
    def share(self) -> float:
        """The unread share of what was examined, or zero where nothing was."""
        return 0.0 if not self.examined else self.unread / self.examined


def _counted_kinds(parameters: Mapping[str, object]) -> str:
    """Return which denominator a rule declared, refusing one this predicate cannot count."""
    denominator = str(parameters.get("denominator") or "")
    if denominator not in {_WORD_OCCURRENCES, _ALL_KINDS}:
        named = denominator or "no denominator"
        message = (
            f"vocabulary_unread_in_scope counts occurrences over {_WORD_OCCURRENCES} or "
            f"{_ALL_KINDS}, and this rule declares {named}; a share with no stated "
            f"denominator is a number that cannot be compared with any other"
        )
        raise PredicateNotEvaluableError(message)
    return denominator


def _legibility(subject: object, parameters: Mapping[str, object]) -> _Legibility:
    """Return how much of a scope's vocabulary the Dictionary read, over the stated kinds.

    The function counts occurrences and not components, which is what the rule's own sentence says.
    A folder whose every name repeats one unknown word is more illegible than one that uses it once.
    """
    denominator = _counted_kinds(parameters)
    read = unread = unexamined = 0
    for records in getattr(subject, "entries", ()):
        bound: dict[str, list[DictionaryBinding]] = defaultdict(list)
        for binding in getattr(records, "bindings", ()):
            if isinstance(binding, DictionaryBinding):
                bound[binding.component_id].append(binding)
        for component in getattr(records, "components", ()):
            if not isinstance(component, Component):
                continue
            if component.component_type == _EXTENSION_COMPONENT:
                continue
            if denominator == _WORD_OCCURRENCES and component.component_type != _WORD_COMPONENT:
                continue
            occurrences = max(_ONE_OCCURRENCE, component.occurrences)
            bindings = bound.get(component.component_id)
            if not bindings:
                unexamined += occurrences
            elif all(
                binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE
                for binding in bindings
            ):
                unread += occurrences
            else:
                read += occurrences
    return _Legibility(read=read, unread=unread, unexamined=unexamined)


@dataclass(frozen=True, slots=True)
class _FailureRate:
    """How often one region failed, in entries rather than in failures."""

    failing: int = 0
    total: int = 0

    @property
    def rate(self) -> float:
        """The failing share of the region, or zero where the region holds nothing."""
        return 0.0 if not self.total else self.failing / self.total


def _failure_class(parameters: Mapping[str, object]) -> FailureClass:
    """Return the class a rule names, refusing one this predicate cannot count."""
    named = str(parameters.get("failure_class") or "")
    if named == FailureClass.INTERPRETATION.value:
        message = (
            "failure_concentrated_in_scope reads what the scan layer recorded, and an "
            "interpretation failure is a rule's own, produced by the pass this flag runs in; "
            "a flag reading one would read a record its own phase is still writing"
        )
        raise PredicateNotEvaluableError(message)
    try:
        return FailureClass(named)
    except ValueError:
        admitted = ", ".join(
            kind.value for kind in FailureClass if kind is not FailureClass.INTERPRETATION
        )
        message = (
            f"failure_concentrated_in_scope counts failures of one class, and this rule names "
            f"{named or 'none'}; the classes it can count are {admitted}"
        )
        raise PredicateNotEvaluableError(message) from None


def _concentration(parameters: Mapping[str, object]) -> float:
    """Read how far above the pool's own rate a region must sit, never below the pool's."""
    value = parameters.get("concentration_factor")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    return max(1.0, float(value))


def _failing(entries: object, wanted: FailureClass) -> _FailureRate:
    """Return how many of a population's entries carry a failure of one class, and how many
    entries the population holds."""
    failing = total = 0
    for records in entries if isinstance(entries, (list, tuple)) else ():
        total += 1
        if any(
            isinstance(failure, ScanError) and failure.failure_class is wanted
            for failure in getattr(records, "failures", ())
        ):
            failing += 1
    return _FailureRate(failing=failing, total=total)


#: The operation whose failure leaves a folder unentered: nothing beneath it was listed.
_UNENTERED_OPERATION = "scandir"


def _unentered(entries: object, wanted: FailureClass) -> tuple[str, ...]:
    """The folders in a population whose listing failed with this class, in canonical order."""
    found = set()
    for records in entries if isinstance(entries, (list, tuple)) else ():
        entry = getattr(records, "entry", None)
        if not isinstance(entry, Entry) or entry.entry_type is not EntryType.DIRECTORY:
            continue
        if any(
            isinstance(failure, ScanError)
            and failure.failure_class is wanted
            and failure.operation == _UNENTERED_OPERATION
            for failure in getattr(records, "failures", ())
        ):
            found.add(entry.entry_id)
    return tuple(sorted(found))


def _failing_entries(entries: object, wanted: FailureClass) -> tuple[str, ...]:
    """The identifiers of the entries that failed, in canonical order, so a finding cites them."""
    found = set()
    for records in entries if isinstance(entries, (list, tuple)) else ():
        entry = getattr(records, "entry", None)
        if not isinstance(entry, Entry):
            continue
        if any(
            isinstance(failure, ScanError) and failure.failure_class is wanted
            for failure in getattr(records, "failures", ())
        ):
            found.add(entry.entry_id)
    return tuple(sorted(found))


class VocabularyUnreadInScope:
    """A scope whose names the Dictionary mostly cannot read.

    This is the engine noticing where it is out of its depth, which is the honest version of a
    coverage figure. A pool-wide unbound rate is a number that nobody can act on. The same rate
    computed per folder points at the two folders that hold a vocabulary nobody has taught it.
    Those are where an hour of a person's attention buys the most.

    The rule is a discovery surface and not a defect report. Unbound tokens are the normal state of
    any corpus that the shipped Dictionary has never seen, and a folder of them is an invitation
    and not a failure. The action is to look at the ranked tokens and teach the ones that mean
    something, which the teaching verbs already do at the pool level.

    The denominator is stated because two exist, and they answer different questions. Counting
    word components asks what fraction of the words are unknown. Counting every component but the
    extension also counts dates, numbers and versions, which are structurally understood while
    intentionally lacking a binding. A date is not an unknown word, and a rule that uses the wrong
    denominator reports a folder of well-dated files as illegible.

    The folder is what is raised, and the tokens are not. Its own sentence says so, and
    ``ingence teach unknown`` already ranks which words are unread over the whole pool. What this
    rule adds is where, which a pool-wide figure cannot say.

    A component that the binder never looked at is counted apart from one it found nothing for.
    Folding the two together would report a project with binding switched off as entirely
    illegible, which is a fact about a setting and not about a vocabulary.
    """

    name = "vocabulary_unread_in_scope"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the scope where the share of what was examined and unread passes the bound."""
        found = _legibility(subject, parameters)
        floor = _at_least(parameters, "minimum_occurrences")
        if found.examined < floor or found.share <= _share(parameters, "maximum_unbound_share"):
            return None
        return Raised(
            values=(_counted_kinds(parameters),),
            inputs=_unread_bindings(subject, parameters),
            reason=(
                f"{found.unread} of {found.examined} occurrences reached no sense, "
                f"a share of {found.share:.2f}"
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the scope is legible, too small to judge, or never looked at."""
        found = _legibility(subject, parameters)
        if not found.examined:
            if found.unexamined:
                return Abstention(
                    reason=(
                        f"the binder looked at none of this region's {found.unexamined} "
                        f"occurrences, which is not the same as finding nothing for them"
                    )
                )
            return Abstention(reason="this region holds no occurrence of a counted kind")
        floor = _at_least(parameters, "minimum_occurrences")
        if found.examined < floor:
            return Abstention(
                reason=(
                    f"{found.examined} occurrences examined, fewer than the {floor} a share "
                    f"needs to mean anything"
                )
            )
        return Abstention(
            reason=(
                f"{found.unread} of {found.examined} occurrences reached no sense, a share of "
                f"{found.share:.2f}, which is at or under "
                f"{_share(parameters, 'maximum_unbound_share'):.2f}"
            )
        )


@dataclass(frozen=True, slots=True)
class _Shared:
    """A folder of one kind's code, shared beside it as an archive of its own name."""

    #: The kind of package whose code the folder mostly holds: ``Python package``.
    kind: str
    #: The archive beside the folder, by name. Named in the reason and never cited: a finding
    #: about a folder cites only what the folder holds, and the archive is its neighbor.
    archive: str
    #: The folder's files that are that kind's code.
    code: tuple[Entry, ...]
    #: How many files the folder holds directly.
    files: int


@dataclass(frozen=True, slots=True)
class _Layout:
    """What one folder's own name and direct children say about whether a program is there."""

    layout_folders: tuple[Entry, ...]
    program_files: tuple[Entry, ...]
    manifests: tuple[Entry, ...]
    files: int
    #: Each known program whose every part the folder directly holds, with the children matched,
    #: in the order the vocabulary lists the programs. The programs come from the vocabulary.
    named: tuple[tuple[str, tuple[Entry, ...]], ...] = ()
    #: Each kind of package whose packaging the folder directly holds: a Python package's
    #: ``setup.py`` beside its code, an R package's description and namespace, a MATLAB class
    #: folder. This is how a laboratory's own package is recognized without its name being known.
    packaged: tuple[tuple[str, tuple[Entry, ...]], ...] = ()
    #: A folder mostly of one kind's code with an archive of its own name beside it.
    shared: _Shared | None = None
    #: Whether the folder's own name ends as packaging names an application bundle.
    bundle_named: bool = False
    #: A few children to cite where only the folder's own name raised it.
    children: tuple[Entry, ...] = ()

    def signals(self, parameters: Mapping[str, object]) -> tuple[str, ...]:
        """Return what raises the folder, most specific first.

        A named program where its parts are all there comes first. Otherwise a kind of package by
        its packaging, then a folder of one kind's code shared as an archive of its own name.
        Otherwise an application bundle by its own name. Otherwise the layout and the share of
        program files, as version 1 read them. There is one kind of value per folder, so a folder
        is one question and not three.
        """
        if self.named:
            return tuple(dict.fromkeys(name for name, _ in self.named))
        if self.packaged:
            return tuple(dict.fromkeys(kind for kind, _ in self.packaged))
        if self.shared is not None:
            return (self.shared.kind,)
        if self.bundle_named:
            return (_APPLICATION_BUNDLE,)
        found = []
        if len(self.layout_folders) >= _at_least(parameters, "minimum_layout_folders"):
            found.append(_LAYOUT_FOLDERS)
        programs = len(self.program_files)
        if (
            programs >= _at_least(parameters, "minimum_program_files")
            and self.files
            and programs / self.files >= _share(parameters, "minimum_program_share")
        ):
            found.append(_PROGRAM_FILES)
        return tuple(found)


#: What the rule raises: which layout signal met its threshold. Two values at most, so every
#: folder raised the same way is one question in the Review queue rather than one per folder.
_LAYOUT_FOLDERS = "layout folders"
#: A folder raised because a person said it is a program.
_BY_ANSWER = "a program, by a person's answer"
_PROGRAM_FILES = "program files"
#: A folder whose own name marks an application bundle and whose parts name no known program.
_APPLICATION_BUNDLE = "application bundle"

#: Records cited per signal. A folder of five thousand headers is shown by a few of them, and
#: the reason gives the whole count.
_CITED_PER_SIGNAL = 5

#: How a release is written after a name: ``-1.2.0``, ``_v3``, `` 2.1rc1``. The grammar of a
#: version, which is the same for everybody's archives, and so a constant and not a setting:
#: ``kelt-0.3.tar.gz`` is the archive of ``kelt`` and of ``kelt-0.3``.
_VERSION_TAIL = re.compile(
    r"[-_ .]v?\d+(?:[._]\d+)*(?:[-_.]?(?:a|b|rc|dev|post)\d*)?$", re.IGNORECASE
)


def _declared_programs(parameters: Mapping[str, object]) -> tuple[ProgramLayout, ...]:
    """Read the programs a rule itself declares, as version 2 held them.

    A mapping from a program's name to its parts, comma-separated, as a rule's mapping parameter
    holds text. Version 3 declares none, because the catalog is vocabulary. A project that
    declares some, as version 2 did, still has them read. Each part is a child's name, a file or
    a folder alike, and a name ending in ``*`` is a prefix, as version 2 read it.
    """
    declared = parameters.get("program_signatures")
    if not isinstance(declared, dict):
        return ()
    found: list[ProgramLayout] = []
    for name, spelled in sorted(declared.items()):
        parts = [part.strip() for part in str(spelled).split(",") if part.strip()]
        if parts:
            found.extend(
                layouts_from(
                    [{"program": str(name), "holds": [[part, f"{part}/"] for part in parts]}],
                    section="rule parameter",
                    minimum_program_parts=1,
                )
            )
    return tuple(found)


def _archive_named_for(
    own: str, siblings: Sequence[Entry], suffixes: Sequence[str]
) -> Entry | None:
    """The first file beside a folder whose name is the folder's own, as an archive's.

    The archive has the same name, or the same name and a release: ``kelt.zip`` and
    ``kelt-0.3.tar.gz`` are both the archive of ``kelt``, and ``kelt-0.3.tar.gz`` is the archive of
    ``kelt-0.3``. The names are compared without case.
    """
    bare = _VERSION_TAIL.sub("", own)
    for sibling in siblings:
        if sibling.entry_type is not EntryType.FILE:
            continue
        name = sibling.name.casefold()
        for suffix in suffixes:
            if not name.endswith(suffix) or len(name) == len(suffix):
                continue
            stem = name[: -len(suffix)]
            if stem in {own, bare} or _VERSION_TAIL.sub("", stem) == own:
                return sibling
    return None


def _shared_code(
    view: FolderView,
    files: int,
    siblings: Sequence[Entry],
    layouts: Sequence[ProgramLayout],
    parameters: Mapping[str, object],
) -> _Shared | None:
    """A folder mostly of one kind's code with an archive of its own name beside it, or None.

    The cheap refusal comes first: no archive of the folder's name beside it, which is nearly every
    folder. Then comes the share, which keeps an experiment zipped beside itself from being read as
    a package because one script sits among its images.
    """
    suffixes = sorted(
        (suffix.casefold() for suffix in _listed(parameters, "archive_suffixes")),
        key=lambda suffix: (-len(suffix), suffix),
    )
    if not suffixes or not files:
        return None
    archive = _archive_named_for(view.own_name, siblings, suffixes)
    if archive is None:
        return None
    best: tuple[int, str, tuple[Entry, ...]] | None = None
    for layout in layouts:
        code = layout.code_in(view)
        if code and (best is None or (-len(code), layout.name) < (-best[0], best[1])):
            best = (len(code), layout.name, code)
    if best is None or best[0] / files < _share(parameters, "minimum_code_share"):
        return None
    return _Shared(kind=best[1], archive=archive.name, code=best[2], files=files)


def _layout_of(
    subject: object, parameters: Mapping[str, object], *, matching: bool = True
) -> _Layout:
    """Read one folder's own name and direct children for the layout of an installed program.

    ``matching`` off reads only the counts an abstention reports, which is the hot path: every
    folder a rule does not raise is explained once, and no layout is matched a second time to
    say what the first matching already found wanting.
    """
    held = getattr(subject, "entries", None)
    if held is None:
        message = "installed_software_in_scope reads a scope, and was handed something else"
        raise PredicateNotEvaluableError(message)
    entries = sorted(
        (
            entry
            for entry in (getattr(records, "entry", None) for records in held)
            if isinstance(entry, Entry)
        ),
        key=lambda entry: (entry.name, entry.entry_id),
    )
    holder = getattr(getattr(subject, "holder", None), "entry", None)
    own = holder.name.casefold() if isinstance(holder, Entry) else ""
    view = FolderView.of(own, entries)
    folder_names = {name.casefold() for name in _listed(parameters, "layout_folder_names")}
    extensions = {
        extension.casefold().removeprefix(".")
        for extension in _listed(parameters, "program_extensions")
    }
    manifest_names = {name.casefold() for name in _listed(parameters, "manifest_names")}
    folders = [entry for entry in view.folders if entry.name.casefold() in folder_names]
    files = [entry for entry in view.files if entry.entry_type is EntryType.FILE]
    programs = [entry for entry in files if (entry.extension or "").casefold() in extensions]
    manifests = [entry for entry in files if entry.name.casefold() in manifest_names]
    # The programs and packages that the vocabulary declares, handed over by the pass: the
    # core's packaging conventions and the programs pack, which a person may switch off.
    handed = tuple(getattr(subject, "layouts", ()) or ()) if matching else ()
    known = (*handed, *_declared_programs(parameters)) if matching else ()
    named: list[tuple[str, tuple[Entry, ...]]] = []
    packaged: list[tuple[str, tuple[Entry, ...]]] = []
    for layout in known:
        met = layout.met_in(view)
        if met is not None:
            (named if layout.names_a_program else packaged).append((layout.name, met))
    shared = None
    if matching and not named and not packaged:
        own_id = holder.entry_id if isinstance(holder, Entry) else None
        beside = (getattr(records, "entry", None) for records in getattr(subject, "siblings", ()))
        shared = _shared_code(
            view,
            len(files),
            tuple(
                entry for entry in beside if isinstance(entry, Entry) and entry.entry_id != own_id
            ),
            [layout for layout in handed if not layout.names_a_program and layout.code],
            parameters,
        )
    suffixes = {suffix.casefold() for suffix in _listed(parameters, "bundle_name_suffixes")}
    bundle_named = bool(own) and any(own.endswith(suffix) and own != suffix for suffix in suffixes)
    return _Layout(
        tuple(folders),
        tuple(programs),
        tuple(manifests),
        len(files),
        named=tuple(named),
        packaged=tuple(packaged),
        shared=shared,
        bundle_named=bundle_named,
        children=tuple(entries[:_CITED_PER_SIGNAL]),
    )


class InstalledSoftwareInScope:
    """A folder laid out the way an installed program or a package is: a scope, not an experiment.

    A laboratory drive holds programs as well as data: a bundled language distribution, a tool's
    executables and libraries, and headers by the thousand. On one laboratory drive, most of the
    stubs, copy classes, candidates and mentions that a run found came from such programs.
    Excluding them by name would break the de novo rule and hide a fifth of a drive from the
    person it belongs to. What
    generalises is the layout, which is the same on every machine because packaging conventions
    made it so: folders named for binaries, libraries and headers side by side, or a folder mostly
    of compiled objects and headers.

    Version 2 recognizes a program from its top folder. A known program's signature is the parts
    it always carries side by side, and it names the program. A folder's own name that ends as
    packaging names an application does the same (``.app``).

    Version 3 recognizes a package by its packaging, and reads the programs from the vocabulary.
    File structure and type identify it. A laboratory's own package carries no name that anybody
    built in, and needs none. Examples are a ``setup.py`` or a ``requirements.txt`` beside
    Python modules, an R package's description and namespace, a MATLAB class folder, and a Maven
    build beside its sources. Each is the same wherever it is. The layouts are Dictionary data:
    the core's packaging conventions and a pack of named scientific suites that a person may switch
    off, handed over by the pass (:attr:`~ingence.infer.subjects.ScopeRecords.layouts`). None is a
    list in Python. A folder mostly of one kind's code with an archive of its own name beside it is
    that kind of package, shared as the archive is. The neighbor's name is read and never cited,
    because a finding about a folder cites only what the folder holds.

    The rule still leaves some folders alone, on measurement. A folder of scripts with no packaging
    is not raised, however many scripts it holds. On one laboratory's folder, the one such folder
    that a share of scripts would raise held a laboratory's experiments beneath it, and everything
    beneath a raised folder is deferred. A script is recognized as a program file by file
    (``program_from_extension``). A packaging file with no code beside it raises nothing, and data
    kept beside it is still data. Files of one size are not read, because that is what uncompressed
    microscopy of one geometry looks like.

    The predicate reads names, types, extensions and counts that the census already holds, of a
    folder's direct children, its own name and the names beside it. It reads nothing inside a file.
    It raises and concludes nothing. A rule that names it as a defeater says what follows for the
    files beneath the folder it raised, and a dive reads them last.
    """

    name = "installed_software_in_scope"
    version = "4"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the folder where a signal meets its threshold, citing a few records each.

        A folder that a person said is not a program is not raised. The answer is in the Record and
        reaches the folder as ``not_a_program``, so inference and the dive both leave it in the
        data's scope.
        """
        if getattr(getattr(subject, "holder", None), "not_a_program", False):
            return None
        if getattr(getattr(subject, "holder", None), "a_program", False):
            # A folder that a person said is a program (version 4): software outside the catalog,
            # asked about by its structure and answered.
            held = sorted(records.entry.entry_id for records in getattr(subject, "entries", ()))
            if held:
                return Raised(
                    values=(_BY_ANSWER,),
                    inputs=tuple(held[:_CITED_PER_SIGNAL]),
                    reason="a person said this folder is a program",
                )
        layout = _layout_of(subject, parameters)
        signals = layout.signals(parameters)
        if not signals:
            return None
        cited: list[str] = []
        clauses: list[str] = []
        if layout.named:
            for name, parts in layout.named:
                cited.extend(entry.entry_id for entry in parts)
                clauses.append(
                    f"it holds {name}'s parts side by side: "
                    + ", ".join(sorted(entry.name for entry in parts))
                )
        elif layout.packaged:
            for kind, parts in layout.packaged:
                cited.extend(entry.entry_id for entry in parts)
                clauses.append(
                    f"{kind}, by the parts it holds side by side: "
                    + ", ".join(sorted(entry.name for entry in parts))
                )
        elif layout.shared is not None:
            shared = layout.shared
            cited.extend(entry.entry_id for entry in shared.code[:_CITED_PER_SIGNAL])
            clauses.append(
                f"{len(shared.code)} of its {shared.files} files are {shared.kind} code, and an "
                f"archive of its own name lies beside it: {shared.archive}"
            )
        elif layout.bundle_named:
            cited.extend(entry.entry_id for entry in layout.children)
            clauses.append("its own name marks an application bundle")
        if _LAYOUT_FOLDERS in signals:
            cited.extend(entry.entry_id for entry in layout.layout_folders)
            clauses.append(
                f"{len(layout.layout_folders)} folders named for a program's layout: "
                + ", ".join(sorted(entry.name for entry in layout.layout_folders))
            )
        if _PROGRAM_FILES in signals:
            cited.extend(
                sorted(entry.entry_id for entry in layout.program_files)[:_CITED_PER_SIGNAL]
            )
            clauses.append(
                f"{len(layout.program_files)} of its {layout.files} files have a program's "
                "extension"
            )
        if layout.manifests:
            cited.extend(entry.entry_id for entry in layout.manifests)
            clauses.append(
                "beside a packaging file: "
                + ", ".join(sorted(entry.name for entry in layout.manifests))
            )
        return Raised(
            values=tuple(signals),
            inputs=tuple(sorted(set(cited))),
            reason="; ".join(clauses),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say how far short of each threshold the folder's layout fell."""
        layout = _layout_of(subject, parameters, matching=False)
        folders_needed = _at_least(parameters, "minimum_layout_folders")
        files_needed = _at_least(parameters, "minimum_program_files")
        reason = (
            f"no known program's or package's parts side by side, no archive of its own name "
            f"beside a folder mostly of code, and no application's name; "
            f"{len(layout.layout_folders)} layout folders where {folders_needed} are asked "
            f"for, and {len(layout.program_files)} program files of {layout.files} where "
            f"{files_needed}, and a share of "
            f"{_share(parameters, 'minimum_program_share'):.2f}, are"
        )
        if layout.manifests:
            reason += "; a packaging file stands here and raises nothing alone"
        return Abstention(reason=reason)


class CopiedInBulk:
    """A folder whose files were copied into it together: born after they were made, at once.

    Copying in bulk is common. A copy keeps each
    file's modification time and gives it a new birth. So a folder copied whole holds files born
    after they were last modified, and born within moments of one another. The rule raises a
    folder where it directly holds ``minimum_files`` or more files, ``minimum_share`` of them born
    after their modification, and their births span ``burst_window_seconds`` or less. The finding
    names the copy's moment, so a person who reads a birth in that folder knows it is the copy's
    and not the file's (``earliest_credible_time`` then reads the modification instead).

    The defaults were measured on two real laboratory folders. Of the folders that hold five or
    more files, those with four in five born after their modification had their births within ten
    minutes in most cases.

    Version 2 asks only where the copy changes an order. A copy is not a reason for a large
    response, and a time raises a question only where it decides an order. Where the rule
    names ``stage_rules``, a copied folder is raised only where the earliest day of a file at a
    later stage comes before the earliest day of a file at one of ``entry_stages``
    (``time_rule``'s days, never seconds). That is analysis that is apparently older than the data
    it analyses. Every other copy is information, concluded on the folder by a classifying rule
    (``copied_in_bulk_from_contents``) and asked about nowhere.
    """

    name = "copied_in_bulk"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the folder where its files were copied in together, naming when."""
        reading = _bulk_copy(subject, parameters)
        if reading is None:
            return None
        births, after, total, cited = reading
        span = (births[-1] - births[0]) / 1e9
        moment = dt.datetime.fromtimestamp(births[0] / 1e9, tz=dt.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        order = ""
        if _declared_strings(parameters, "stage_rules"):
            inverted = _stages_out_of_order(subject, parameters)
            if inverted is None:
                return None
            order = (
                f"; and a file at a later stage first existed on {inverted[1]}, before its raw "
                f"data's earliest day, {inverted[0]}: which came first decides an order"
            )
        return Raised(
            values=("copied_in_bulk",),
            inputs=cited,
            reason=(
                f"{after} of its {total} files were born after they were last modified, and "
                f"their births span {span:.0f} s from {moment}: a copy made then, so a birth "
                f"here is the copy's and the modification is the file's{order}"
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say which of the three conditions the folder's files did not meet."""
        if (
            _declared_strings(parameters, "stage_rules")
            and _bulk_copy(subject, parameters) is not None
        ):
            return Abstention(
                reason=(
                    "copied in bulk, and every later stage's earliest day follows its raw data's: "
                    "information, not a question"
                )
            )
        files = _dated_files(subject)
        return Abstention(
            reason=(
                f"{len(files)} files with a birth and a modification, where "
                f"{_at_least(parameters, 'minimum_files')} born after their modification within "
                f"{_whole(parameters, 'burst_window_seconds', 600)} s, and a share of "
                f"{_share(parameters, 'minimum_share'):.2f}, are asked for"
            )
        )


def _dated_files(subject: object) -> list[Entry]:
    held = getattr(subject, "entries", None)
    if held is None:
        message = "copied_in_bulk reads a scope, and was handed something else"
        raise PredicateNotEvaluableError(message)
    return [
        entry
        for entry in (getattr(records, "entry", None) for records in held)
        if isinstance(entry, Entry)
        and entry.entry_type is EntryType.FILE
        and entry.created_ns is not None
        and entry.modified_ns is not None
        and str(entry.created_time_source) == "birthtime"
    ]


def _bulk_copy(
    subject: object, parameters: Mapping[str, object]
) -> tuple[list[int], int, int, tuple[str, ...]] | None:
    """The copied files' births, how many, of how many, and a few to cite; None if no copy."""
    files = _dated_files(subject)
    if len(files) < _at_least(parameters, "minimum_files"):
        return None
    copied = [entry for entry in files if (entry.created_ns or 0) > (entry.modified_ns or 0)]
    if len(copied) / len(files) < _share(parameters, "minimum_share"):
        return None
    births = sorted(int(entry.created_ns or 0) for entry in copied)
    window = _whole(parameters, "burst_window_seconds", 600) * 1_000_000_000
    if births[-1] - births[0] > window:
        return None
    cited = tuple(sorted(entry.entry_id for entry in copied)[:_CITED_PER_SIGNAL])
    return births, len(copied), len(files), cited


def _stages_out_of_order(
    subject: object, parameters: Mapping[str, object]
) -> tuple[str, str] | None:
    """The raw data's earliest day and a later stage's earlier one, where a copied folder's files
    put a later stage first; None where every later stage follows (days, never seconds)."""
    stage_rules = set(_declared_strings(parameters, "stage_rules"))
    entry_stages = set(_declared_strings(parameters, "entry_stages"))
    time_rule = str(parameters.get("time_rule", "earliest_credible_time"))
    first_entry: str | None = None
    first_later: str | None = None
    for records in getattr(subject, "entries", ()):
        stages: set[str] = set()
        day: str | None = None
        for claim in getattr(records, "claims", ()):
            if claim.object_reference is None or str(claim.resolution_status) != "resolved":
                continue
            if claim.rule_id in stage_rules:
                stages.add(str(claim.object_reference))
            elif claim.rule_id == time_rule:
                try:
                    day = str(json.loads(claim.object_reference)["at"])[:10]
                except (ValueError, KeyError, TypeError):
                    day = None
        if day is None or not stages:
            continue
        if stages & entry_stages:
            first_entry = day if first_entry is None else min(first_entry, day)
        if stages - entry_stages:
            first_later = day if first_later is None else min(first_later, day)
    if first_entry is not None and first_later is not None and first_later < first_entry:
        return first_entry, first_later
    return None


#: What type detection records about a file's type, all under one prefix: whether
#: the name's extension agrees with the content, and each detector's opinion of
#: the content, keyed by the detector's name.
_DETECTION = "type."
_EXTENSION_AGREES = "type.extension_agrees"
_OPINION = "type.opinion."


def _observations(subject: object) -> tuple[Observation, ...]:
    held = getattr(subject, "observations", subject)
    if not isinstance(held, (list, tuple)):
        return ()
    return tuple(item for item in held if isinstance(item, Observation))


def _program_format(subject: object, parameters: Mapping[str, object]) -> str | None:
    """Return the format an entry's extension names, where that settles a ZIP's use.

    The vocabulary decides before a person is asked. A ZIP under an extension that the Dictionary
    names as a format, such as a presentation package or a statistics project, is that program's
    own format written on a ZIP, because a person who writes an archive names it an archive. So the
    extension's binding settles it wherever it resolved to a format that is not one of
    ``archive_types``. The function returns nothing where the rule declares no archive types. That
    keeps a rule written before this reading raising exactly what it raised.
    """
    archives = _declared_strings(parameters, "archive_types")
    if not archives:
        return None
    for binding in getattr(subject, "bindings", ()):
        if (
            isinstance(binding, DictionaryBinding)
            and binding.constraint is not None
            and binding.constraint.split(":", 1)[0] == "extension"
            and binding.resolution_status is ResolutionStatus.RESOLVED
            and binding.theme_kind == "format"
            and binding.term is not None
            and binding.term not in archives
        ):
            return binding.term
    return None


class ArchiveNamedButUnlisted:
    """An archive whose name says what it holds, and whose members nobody has listed: asked.

    A name is weaker evidence than a listing. ``RoiSet.zip`` is probably Fiji's regions of
    interest, and a ZIP that a person named for its regions sometimes holds something else. So
    this predicate raises the named sense where an entry's extension binds one of
    ``archive_senses``, the rest of its name binds one of ``named_senses`` (resolved, or among a
    conflicting word's candidates), and no dive has listed its members. The result is a question
    that a dive or a person answers. Once a dive lists the members, ``format_from_archive_members``
    concludes, or says why not, and this predicate is quiet.
    """

    name = "archive_named_but_unlisted"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the senses the name gives an unlisted archive, or None."""
        entry = getattr(subject, "entry", None)
        if not isinstance(entry, Entry) or entry.entry_type is EntryType.DIRECTORY:
            return None
        if any(item.key == _MEMBER_LISTING for item in _observations(subject)):
            return None
        bindings = [
            binding
            for binding in getattr(subject, "bindings", ())
            if isinstance(binding, DictionaryBinding) and binding.term is not None
        ]
        archives = _declared_strings(parameters, "archive_senses")
        if not any(
            _in_extension(binding)
            and binding.resolution_status is ResolutionStatus.RESOLVED
            and binding.term in archives
            for binding in bindings
        ):
            return None
        named = _declared_strings(parameters, "named_senses")
        said = {
            binding.binding_id: terms
            for binding in getattr(subject, "bindings", ())
            if isinstance(binding, DictionaryBinding) and not _in_extension(binding)
            for terms in (_senses_named(binding, named),)
            if terms
        }
        if not said:
            return None
        return Raised(
            values=tuple(sorted({term for terms in said.values() for term in terms})),
            inputs=(entry.entry_id, *sorted(said)),
            reason=(
                "its name says what the archive holds and no dive has listed its members; a "
                "listing would say whether it is that program's own archive"
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether its members are listed, or its name or extension says nothing asked."""
        if any(item.key == _MEMBER_LISTING for item in _observations(subject)):
            return Abstention(reason="a dive listed its members, which settle what it holds")
        return Abstention(
            reason="its extension is no archive the rule names, or its name names nothing it asks"
        )


def _senses_named(binding: DictionaryBinding, named: frozenset[str]) -> frozenset[str]:
    """The named senses a word is bound to, or stands between (a conflicting word's candidates
    carry their identifiers, which each named term is spelled into at the candidate's scope)."""
    if binding.resolution_status is ResolutionStatus.RESOLVED:
        return frozenset({binding.term}) & named if binding.term else frozenset()
    if binding.resolution_status is not ResolutionStatus.EXAMINED_CONFLICTING:
        return frozenset()
    return frozenset(
        term
        for alternative in binding.alternatives
        for term in named
        if alternative.term_id
        == dictionary_term_id(
            term=term, scope=alternative.scope, origin=str(alternative.origin.value)
        )
    )


#: What the archive reading records of every member it lists (version 2).
_MEMBER_LISTING = "archive.member_extensions"


def _in_extension(binding: DictionaryBinding) -> bool:
    """Whether a binding is the extension's, which binds only to a format."""
    return binding.constraint is not None and binding.constraint.split(":", 1)[0] == "extension"


class ExtensionContradictsContent:
    """A file whose bytes carry the mark of one format and whose name carries another.

    This is the cheapest useful finding that type detection produces, and it is the fact that
    retracts a format read from an extension. An extension is what somebody wrote when naming the
    file. A signature is fixed by the format's own specification. Where the two disagree, the file
    was misnamed or renamed, and nothing about either reading says which, so the predicate raises
    it and does not resolve it.

    Detection decides agreement in two ways, and only one of them is a contradiction. Where the
    bytes carry a signature, the extension is compared against the extensions that the signature's
    format declares. Everywhere else, the strongest detector's media type is compared as a string
    with a hint read from the extension, and two vocabularies spell one format differently. An
    example is a script type against a language type, or a generic ``text/plain`` against a
    structured type filed under another top-level name. Run over a real collection of laboratory
    folders, every disagreement came from that second comparison, on files that included the
    engine's own rule files and schemas. So a disagreement counts only where the opinion of the
    detector that the rule names stands behind it, and the rule names the signature.

    That relies on every signature declaring its extensions. Every signature in the table does,
    and a test holds it. A signature that declared none would have its agreement decided as a
    string, which is the half that this predicate refuses.

    A general-purpose container is not a contradiction, and a rule says which half it raises. A
    format built on a container, such as HDF5 or ZIP, carries its own application extension. So a
    container's signature under that extension is an observation and not a contradiction. On one
    real collection, the rule raised 43 files. Every one of them was a format built on HDF5, PDF,
    gzip, ZIP, OLE storage or ISO media under its own application extension. As an observation the
    rule was right 43 times, and as a contradiction never, so it also retracted 42 format claims
    that were correct.

    ``raises`` says which reading a rule takes:

    - ``any_disagreement`` raises the detector's media type whatever it is, which is what the
      shipped rule does;
    - ``contradiction`` raises it only where it is not one of ``container_media_types``, and that
      is the reading that retracts a format read from an extension;
    - ``container`` raises only the container half, as ``built_on_container:<media type>``, or
      ``container_unclear:<media type>`` for a container in ``unclear_media_types`` whose use a
      laboratory must say. An ImageJ ROI set and a LiCor image are ZIPs that a program writes. So
      the engine asks the user early what such a ZIP is, as a question ranked by how many files
      raise it.

    A contradiction raises the media type alone, because it is read as a classification is where
    it stands as a defeater. The container reading is nobody's defeater and carries its own
    records and reason.

    The predicate is evaluable wherever a content pass has run, and unexamined wherever one has
    not, because detection never runs in a census.
    """

    name = "extension_contradicts_content"
    #: 3 reads the whole entry, so a ZIP under an extension the Dictionary names as a format is
    #: that program's own container and not a question. 2 admitted the container reading. 1 raised
    #: every disagreement alike, and a rule that names no ``raises`` and no ``archive_types`` still
    #: gets exactly that.
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return what the named detector says the content is, under this rule's reading."""
        observations = _observations(subject)
        if not _disagrees(observations):
            return None
        opinions = _opinions(observations, detector=str(parameters["detector"]))
        if not opinions:
            return None
        found = opinions[0]
        raises = _raises(parameters)
        containers = _declared_strings(parameters, "container_media_types")
        unclear = _declared_strings(parameters, "unclear_media_types")
        if raises == _ANY_DISAGREEMENT:
            return found
        if raises == _CONTRADICTION:
            return None if found in containers or found in unclear else found
        if found not in containers and found not in unclear:
            return None
        marker = _CONTAINER_UNCLEAR if found in unclear else _BUILT_ON_CONTAINER
        named = _program_format(subject, parameters) if marker == _CONTAINER_UNCLEAR else None
        if named is not None:
            marker = _BUILT_ON_CONTAINER
        return Raised(
            values=(f"{marker}:{found}",),
            inputs=tuple(
                observation.observation_id
                for observation in observations
                if observation.key == _EXTENSION_AGREES
                or (observation.value_text == found and observation.key.startswith(_OPINION))
            ),
            reason=(
                f"its bytes carry {found} and its extension names {named}, a program's own "
                f"format written on it rather than an archive"
                if named is not None
                else f"its bytes carry {found}, a container its format is written on rather than "
                f"a reading that contradicts the extension"
                if marker == _BUILT_ON_CONTAINER
                else f"its bytes carry {found}, which a program writes as its own format and a "
                f"person also writes as an archive; which one it is here is a laboratory's to say"
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether detection never ran, agreed, disagreed as a string, or found a container."""
        observations = _observations(subject)
        if not any(observation.key.startswith(_DETECTION) for observation in observations):
            return Abstention(
                status=ResolutionStatus.UNEXAMINED,
                reason="type detection has not read this file's content",
            )
        agreement = tuple(
            observation.observation_id
            for observation in observations
            if observation.key == _EXTENSION_AGREES
        )
        if not agreement:
            return Abstention(reason="type detection had nothing to compare the extension with")
        if _disagrees(observations):
            detector = str(parameters["detector"])
            opinions = _opinions(observations, detector=detector)
            raises = _raises(parameters)
            containers = _declared_strings(parameters, "container_media_types") | _declared_strings(
                parameters, "unclear_media_types"
            )
            if opinions and raises == _CONTRADICTION and opinions[0] in containers:
                return Abstention(
                    inputs=agreement,
                    reason=(
                        f"its bytes carry {opinions[0]}, a container a format is written on, "
                        f"which this rule does not read as a contradiction"
                    ),
                )
            if opinions and raises == _CONTAINER and opinions[0] not in containers:
                return Abstention(
                    inputs=agreement,
                    reason=(
                        f"its bytes carry {opinions[0]}, which is no container, so what this "
                        f"rule reads is not there to read"
                    ),
                )
            return Abstention(
                inputs=agreement,
                reason=f"the extension disagrees with a reading no {detector} stands behind",
            )
        return Abstention(inputs=agreement, reason="the extension and the content agree")


#: What a rule may read of a disagreement between an extension and a signature.
_ANY_DISAGREEMENT = "any_disagreement"
_CONTRADICTION = "contradiction"
_CONTAINER = "container"

#: How the container reading spells what it raises.
_BUILT_ON_CONTAINER = "built_on_container"
_CONTAINER_UNCLEAR = "container_unclear"


def _raises(parameters: Mapping[str, object]) -> str:
    """Return which half of a disagreement a rule reads, refusing a reading there is none of."""
    declared = str(parameters.get("raises") or _ANY_DISAGREEMENT)
    if declared not in (_ANY_DISAGREEMENT, _CONTRADICTION, _CONTAINER):
        message = (
            f"extension_contradicts_content reads a disagreement as {_ANY_DISAGREEMENT}, "
            f"{_CONTRADICTION} or {_CONTAINER}, and this rule asks for {declared!r}"
        )
        raise PredicateNotEvaluableError(message)
    return declared


def _declared_strings(parameters: Mapping[str, object], name: str) -> frozenset[str]:
    """Return a string list a rule declared, as nothing where it declares none.

    Media types for the container reading, phrases for a note in a file's own text: a list of
    strings a rule names, read one way so a rule naming one thing cannot be read two ways.
    """
    declared = parameters.get(name)
    if declared is None:
        return frozenset()
    if isinstance(declared, str):
        return frozenset({declared})
    if isinstance(declared, (list, tuple, set, frozenset)):
        return frozenset(str(item) for item in declared if item)
    message = f"{name} is a list of strings, and this rule declares {declared!r}"
    raise PredicateNotEvaluableError(message)


def _disagrees(observations: Sequence[Observation]) -> bool:
    """Whether detection recorded the extension as disagreeing with the content."""
    return any(
        observation.key == _EXTENSION_AGREES and observation.value_boolean is False
        for observation in observations
    )


def _opinions(observations: Sequence[Observation], *, detector: str) -> list[str]:
    """Return one detector's opinions of the content, in canonical order."""
    key = f"{_OPINION}{detector}"
    return sorted(
        {
            observation.value_text
            for observation in observations
            if observation.key == key and observation.value_text
        }
    )


#: The one reading ``readings_conflict`` compares today.
_DICTIONARY_BINDING = "dictionary_binding"


#: The component type an indexed atom writes. One spelling, shared with the grouping
#: filter that reads the same components, because two are two places to drift.
_INDEXED_COMPONENT = "indexed"


def _unplaced_labels(subject: object) -> dict[str, _LabelRun]:
    """Return every indexed label in a scope the Dictionary places nowhere, and where it sits.

    Placed nowhere means that every binding on the component reached no sense, neither from the
    component's whole text nor from its label. A resolved or a conflicting binding is the
    Dictionary having an answer. A component with no binding row at all is one that the binder
    never looked at, which is distinct from finding nothing.
    """
    found: dict[str, _LabelRun] = defaultdict(_LabelRun)
    for records in getattr(subject, "entries", ()):
        entry = getattr(records, "entry", None)
        if not isinstance(entry, Entry):
            continue
        bound: dict[str, list[DictionaryBinding]] = defaultdict(list)
        for binding in getattr(records, "bindings", ()):
            if isinstance(binding, DictionaryBinding):
                bound[binding.component_id].append(binding)
        for component in getattr(records, "components", ()):
            if not isinstance(component, Component):
                continue
            if component.component_type != _INDEXED_COMPONENT:
                continue
            bindings = bound.get(component.component_id)
            if not bindings or any(
                binding.resolution_status is not ResolutionStatus.EXAMINED_NO_EVIDENCE
                for binding in bindings
            ):
                continue
            split = split_indexed(component.normalized_value)
            if split is None:
                continue
            label, index = split
            run = found[label]
            run.carried[index].append(component)
            run.entries.add(entry.entry_id)
            if entry.parent_entry_id is not None:
                run.folders.add(entry.parent_entry_id)
    return dict(found)


def _qualifying_labels(subject: object, parameters: Mapping[str, object]) -> dict[str, _LabelRun]:
    """Return the unplaced labels shaped like a replicate marker rather than a version number.

    Three conditions hold, and each does a different job. The label is numbered from one without a
    gap, because a set of replicates is numbered from its first. It reaches at least two and no
    further than the rule's maximum, because one is not a run and an unbounded count is a version
    number. It appears in at least the rule's number of folders, which separates a laboratory that
    marks replicates this way from one folder that happened to hold a ``V1``.
    """
    maximum = _at_least(parameters, "maximum_index")
    folders = _at_least(parameters, "minimum_folders")
    return {
        label: run
        for label, run in _unplaced_labels(subject).items()
        if 2 <= run.run_to <= maximum and len(run.folders) >= folders
    }


def _refuse_all_but_the_dictionary(parameters: Mapping[str, object]) -> None:
    """Refuse a reading other than a Dictionary binding's, whose comparison is not implemented."""
    reading = str(parameters.get("reading") or "")
    if reading != _DICTIONARY_BINDING:
        named = reading or "an unnamed reading"
        message = (
            f"readings_conflict compares two readings of {named}, and comparing them is not "
            f"implemented; only a {_DICTIONARY_BINDING}'s senses are compared"
        )
        raise PredicateNotEvaluableError(message)


def _conflicting(subject: object) -> tuple[DictionaryBinding, ...]:
    """The bindings on an entry's records that stand between senses, in canonical order.

    A tie between domains in too few files is not raised (``readings_conflict`` version 3). The
    resolver marks a word that two domains spell differently, whose folder's files lean neither
    way and which fewer files carry than ``dictionary.domain_skew.question_above_files``, with
    :data:`DOMAIN_TIE_CONSTRAINT`. The binding stays conflicting and asks nothing.
    """
    return tuple(
        sorted(
            (
                binding
                for binding in getattr(subject, "bindings", ())
                if isinstance(binding, DictionaryBinding)
                and binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
                and binding.constraint != DOMAIN_TIE_CONSTRAINT
            ),
            key=lambda binding: binding.binding_id,
        )
    )


def _named_terms(parameters: Mapping[str, object]) -> frozenset[str]:
    """Read the terms a doubt rule names, refusing a rule that names none."""
    value = parameters.get("senses")
    if isinstance(value, str):
        names = frozenset({value} if value else ())
    elif isinstance(value, (list, tuple, set, frozenset)):
        names = frozenset(str(item) for item in value if item)
    else:
        names = frozenset()
    if not names:
        message = (
            "authored_name_doubt counts the doubt terms its rule names, and this rule names "
            "none, so it would raise any quality term, filesystem noise included"
        )
        raise PredicateNotEvaluableError(message)
    return names


def _free_bindings(subject: object) -> tuple[DictionaryBinding, ...]:
    """The bindings of a name's own tokens, without what the extension slot bound."""
    if not isinstance(subject, (list, tuple)):
        return ()
    return tuple(
        binding
        for binding in subject
        if isinstance(binding, DictionaryBinding) and binding.constraint is None
    )


def _terms_found(subject: object, wanted: frozenset[str]) -> frozenset[str]:
    """The named terms a name's own tokens resolved to, each counted once."""
    return frozenset(
        binding.term
        for binding in _free_bindings(subject)
        if binding.resolution_status is ResolutionStatus.RESOLVED
        and binding.term is not None
        and binding.term in wanted
    )


def _instant(parameters: Mapping[str, object], key: str) -> int | None:
    """Read a date a rule names, as nanoseconds since the epoch, or None where it names none.

    Written as a date rather than as a number, because the rendered sentence is what a person
    reads and a nanosecond count is not a sentence. A date with no zone is read as UTC, which is
    what a rule declaring a boundary means: a boundary that moves with the reader's timezone is
    not a boundary.
    """
    written = parameters.get(key)
    if not isinstance(written, str) or not written.strip():
        return None
    try:
        moment = dt.datetime.fromisoformat(written.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return int(moment.timestamp() * 1_000_000_000)


def _whole(parameters: Mapping[str, object], key: str, default: int) -> int:
    """Read a whole number a rule declares, never below zero.

    Separate from :func:`_at_least`, which floors at one. A byte ceiling of zero is the meaningful
    default for an empty file, and flooring it at one would admit every one-byte file.
    """
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def _at_least(parameters: Mapping[str, object], key: str) -> int:
    """Read a minimum a rule declares, never below one."""
    value = parameters.get(key, 1)
    if isinstance(value, bool) or not isinstance(value, int):
        return 1
    return max(1, value)


#: What a doubt rule raises: a name its doubt terms raise, or a name whose only doubt terms are
#: low-strength, for a person to confirm.
_DOUBT = "doubt"
_CONFIRMATION = "confirmation"


def _listed(parameters: Mapping[str, object], key: str) -> frozenset[str]:
    """Read a list of names a rule may leave out, tolerating the single-string spelling."""
    value = parameters.get(key)
    if isinstance(value, str):
        return frozenset({value} if value else ())
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item) for item in value if item)
    return frozenset()


def _raising(parameters: Mapping[str, object]) -> str:
    """Return what a doubt rule raises, refusing anything but doubt or confirmation."""
    declared = parameters.get("raises")
    if declared is None or declared == _DOUBT:
        return _DOUBT
    if declared == _CONFIRMATION:
        return _CONFIRMATION
    message = (
        f"authored_name_doubt raises a name for doubt or for confirmation, and this rule asks "
        f"for {declared!r}"
    )
    raise PredicateNotEvaluableError(message)


def _graded(found: frozenset[str], parameters: Mapping[str, object]) -> frozenset[str]:
    """Return the terms that count: all of them beside one that stands alone, or only low ones.

    Under doubt, a low-strength term counts only where a named term outside the low list is
    found too. Under confirmation, the terms count only where every one found is low-strength.
    """
    standing = found - _listed(parameters, "low_strength_senses")
    if _raising(parameters) == _CONFIRMATION:
        return found if found and not standing else frozenset()
    return found if standing else frozenset()


@declare(
    "content_holds_a_phrase",
    version="1",
    summary="Raise a file whose own text holds one of the phrases a rule names",
    effect=RuleEffect.FLAGS,
    reads=Reads.OBSERVATIONS,
    parameters=("phrases",),
    support_families=("byte_scan",),
)
def _build_content_holds_a_phrase() -> Predicate:
    return ContentHoldsAPhrase()


@declare(
    "archive_named_but_unlisted",
    version="1",
    summary="Raise an archive whose name says what it holds, until a dive lists its members",
    effect=RuleEffect.FLAGS,
    reads=Reads.RECORDS,
    parameters=("archive_senses", "named_senses"),
)
def _build_archive_named_but_unlisted() -> Predicate:
    return ArchiveNamedButUnlisted()


@declare(
    "content_unreadable",
    version="7",
    summary="Raise a file whose content cannot be read, saying which way it is out of reach",
    effect=RuleEffect.FLAGS,
    reads=Reads.ENTRY_AMONG_SIBLINGS,
    parameters=("minimum_words", "note_extensions", "notes_as_names", "written_before"),
)
def _build_content_unreadable() -> Predicate:
    return ContentUnreadable()


@declare(
    "extension_contradicts_content",
    version="3",
    summary="Raise a file whose content contradicts its extension, or names the container it uses",
    effect=RuleEffect.FLAGS,
    reads=Reads.RECORDS,
    parameters=(
        "archive_types",
        "container_media_types",
        "detector",
        "raises",
        "unclear_media_types",
    ),
    support_families=("type_detection",),
)
def _build_extension_contradicts_content() -> Predicate:
    return ExtensionContradictsContent()


@declare(
    "authored_name_doubt",
    version="2",
    summary="Raise an entry whose own name carries a term signalling doubt",
    effect=RuleEffect.FLAGS,
    reads=Reads.BINDINGS,
    parameters=("low_strength_senses", "minimum_terms", "raises", "senses"),
)
def _build_authored_name_doubt() -> Predicate:
    return AuthoredNameDoubt()


@declare(
    "group_incomplete_against_workflow",
    version="1",
    summary="Raise a group missing a step the declared workflow expects",
    effect=RuleEffect.FLAGS,
    reads=Reads.GROUP,
    parameters=("required_stages",),
)
def _build_group_incomplete_against_workflow() -> Predicate:
    return GroupIncompleteAgainstWorkflow()


@declare(
    "group_below_expected_size",
    version="1",
    summary="Raise a group holding fewer members than expected",
    effect=RuleEffect.FLAGS,
    reads=Reads.GROUP,
    parameters=("expected_members", "member_respect"),
)
def _build_group_below_expected_size() -> Predicate:
    return GroupBelowExpectedSize()


@declare(
    "unverified_substitution_candidate",
    version="1",
    summary="Raise entries that look identical from outside and were never compared",
    effect=RuleEffect.FLAGS,
    reads=Reads.GROUP,
    parameters=("ignore_organising_folders", "member_respect", "minimum_members"),
)
def _build_unverified_substitution_candidate() -> Predicate:
    return UnverifiedSubstitutionCandidate()


@declare(
    "readings_conflict",
    version="3",
    summary="Raise a subject whose two readings disagree with nothing to separate them",
    effect=RuleEffect.FLAGS,
    reads=Reads.RECORDS,
    parameters=("minimum_alternatives", "reading", "tolerance_seconds"),
    # A clock is temporal evidence and this is a flagging rule, which may read one.
    # The prohibition is on positive support for a proposed edge, not on the
    # evidence, and a conflict between two clocks is the finding rather than a
    # reason to believe two files are related.
    support_families=("clock_agreement",),
)
def _build_readings_conflict() -> Predicate:
    return ReadingsConflict()


@declare(
    "stated_count_may_mark_replicates",
    version="2",
    summary="Raise a name stating a replicate count, as a cardinal or uncertainly as an ordinal",
    effect=RuleEffect.FLAGS,
    reads=Reads.RECORDS,
    parameters=(
        "derivation_rules",
        "maximum_count",
        "observation_rules",
        "stage_rules",
        "theme_kind",
    ),
)
def _build_stated_count_may_mark_replicates() -> Predicate:
    return StatedCountMayMarkReplicates()


@declare(
    "indexed_label_may_mark_replicates",
    version="1",
    summary="Raise a label the Dictionary misses that is numbered like a replicate set",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=("maximum_index", "minimum_folders"),
)
def _build_indexed_label_may_mark_replicates() -> Predicate:
    return IndexedLabelMayMarkReplicates()


@declare(
    "failure_concentrated_in_scope",
    version="2",
    summary="Raise a scope failing far more often than the pool, and name the class",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=("concentration_factor", "failure_class", "minimum_failures"),
)
def _build_failure_concentrated_in_scope() -> Predicate:
    return FailureConcentratedInScope()


@declare(
    "installed_software_in_scope",
    version="4",
    summary="Raise a folder laid out the way an installed program or a package is",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=(
        "program_signatures",
        "bundle_name_suffixes",
        "layout_folder_names",
        "minimum_layout_folders",
        "program_extensions",
        "minimum_program_files",
        "minimum_program_share",
        "manifest_names",
        "archive_suffixes",
        "minimum_code_share",
    ),
    vocabulary=(VocabularyRead.PROGRAM_LAYOUTS,),
)
def _build_installed_software_in_scope() -> Predicate:
    return InstalledSoftwareInScope()


@declare(
    "copied_in_bulk",
    version="2",
    summary="Raise a folder whose files were copied into it together",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=(
        "burst_window_seconds",
        "entry_stages",
        "minimum_files",
        "minimum_share",
        "stage_rules",
        "time_rule",
    ),
)
def _build_copied_in_bulk() -> Predicate:
    return CopiedInBulk()


@declare(
    "vocabulary_unread_in_scope",
    version="1",
    summary="Raise a scope whose names the Dictionary mostly cannot read",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=("denominator", "maximum_unbound_share", "minimum_occurrences"),
)
def _build_vocabulary_unread_in_scope() -> Predicate:
    return VocabularyUnreadInScope()


class StageDisagreesWithADerivation:
    """Raise a file whose own stage disagrees with a derivation proposed into it.

    Derivations come before stages, and a file made from another is later than it. The name and
    format rules keep their evidence. So a file can be proposed as made from another by the
    evidence that found the pair, while its own name or format states a stage before its source's,
    or states where data begins while its source states none. That is a conflict that a person is
    shown and that is never settled silently. The derivation stands as proposed, and the stage
    stands as concluded.

    The rule raises as few questions as it can (version 2). A question is the rule raising it and
    the value raised. So the value is the class of like conflicts: the derivation's rule, the
    file's stage, its source's stage and the folder. Every file of one class is one question.
    Siblings are not a conflict. Where the two ends state one rung, nothing is raised.

    The rule is read after the proposing phase (``Reads.DERIVED_FROM``): the derivations proposed
    into the file, each with the stages its source holds. A stage is one that ``stage_transitions``
    names, on the rung its order gives.
    """

    name = "stage_disagrees_with_a_derivation"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return each class of conflict the file's own stages make with its derivations."""
        derivations = tuple(getattr(subject, "derivations", ()))
        entry = getattr(subject, "entry", None)
        if not derivations or entry is None:
            return None
        declared = parameters.get("stage_transitions")
        transitions = (
            {str(before): str(after) for before, after in declared.items()}
            if isinstance(declared, dict)
            else {}
        )
        rung = _stage_rungs(transitions)
        own: dict[str, str] = {}
        for claim in sorted(getattr(subject, "claims", ()), key=lambda claim: claim.claim_id):
            if (
                claim.predicate.startswith("classifies.")
                and claim.resolution_status is ResolutionStatus.RESOLVED
                and claim.object_reference in rung
            ):
                own.setdefault(str(claim.object_reference), claim.claim_id)
        if not own:
            return None
        top = max(rung[stage] for stage in own)
        latest = sorted(stage for stage in own if rung[stage] == top)
        folder = str(entry.relative_path).replace("\\", "/").rpartition("/")[0]
        classes: dict[str, list[str]] = {}
        for derivation in derivations:
            held = [stage for stage, _ in derivation.source_stages if stage in rung]
            source_top = max((rung[stage] for stage in held), default=None)
            if source_top is None and top != 0:
                continue
            if source_top is not None and top >= source_top:
                continue
            value = canonical_json(
                {
                    "rule": derivation.rule_id,
                    "stage": " / ".join(latest),
                    "made_from": " / ".join(
                        sorted(stage for stage in held if rung[stage] == source_top)
                    ),
                    "folder": folder,
                }
            )
            classes.setdefault(value, []).append(derivation.source_name)
        if not classes:
            return None
        named = sorted({name for names in classes.values() for name in names})
        return Raised(
            values=tuple(sorted(classes)),
            inputs=tuple(sorted(own[stage] for stage in latest)),
            reason=(
                f"it states {' and '.join(latest)}, and was proposed as made from "
                + ", ".join(named[:3])
                + (f" and {len(named) - 3} more" if len(named) > 3 else "")
            ),
        )


def _stage_rungs(transitions: Mapping[str, str]) -> dict[str, int]:
    """Each stage's rung in the order the transitions give: a stage no step reaches is rung 0."""
    rung = dict.fromkeys(set(transitions) | set(transitions.values()), 0)
    for _ in range(len(rung)):
        changed = False
        for before, after in transitions.items():
            if rung[after] < rung[before] + 1:
                rung[after] = rung[before] + 1
                changed = True
        if not changed:
            break
    return rung


@declare(
    "stage_disagrees_with_a_derivation",
    version="2",
    summary="Raise a file whose own stage is at or before the stage of a file it was made from",
    effect=RuleEffect.FLAGS,
    reads=Reads.DERIVED_FROM,
    parameters=("stage_transitions",),
    reach="attached",
)
def _build_stage_disagrees_with_a_derivation() -> Predicate:
    return StageDisagreesWithADerivation()
