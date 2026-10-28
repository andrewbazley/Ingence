"""Binding a rule's declaration to the code that evaluates it.

A rule is a declaration. The predicate is the code that decides whether the declaration holds of
something. Keeping them apart is the whole of the project's central claim: the code is the
interpreter and the rules are the product. A rule must be readable, editable and switchable without
touching Python, and a predicate must be replaceable without rewriting the rules that use it.

Nothing calls a predicate by name, exactly as nothing calls an extractor by name. A rule names a
predicate in its ``predicate`` field, the loader refuses a rule whose predicate is not registered,
and :data:`PREDICATES` is the only way to reach an implementation. Deleting a predicate is deleting
a module and a registration line. The rules that depended on it fail to load loudly and do not
silently abstain.

A predicate declares which evidence families it may contribute, and that is not decoration. A rule
with ``effect: proposes`` may not take a window or a burst identifier as a scoring input. Two
unrelated projects running in parallel overlap perfectly, and temporal overlap is not evidence of
relatedness. The constraint is easy to violate, because a timeline makes co-occurrence visually
persuasive in exactly the way that invites it. Declaring families lets the loader refuse the
violation, so a reviewer does not have to catch it. See :data:`TEMPORAL_FAMILIES` and
:mod:`ingence.rulebook.load`.

A predicate declares what it reads, beside its effect and its families. What a predicate is handed
is decided by the rule's effect and narrowed to that declaration, which is
:mod:`ingence.infer.subjects`. The declaration is on the registration and not on the instance, for
the reason the families are. A rule whose effect could never hand its predicate what it reads is
refused at load, before the predicate is built or can be.

A predicate declares the parameters it reads, and a rule may declare only those. The loader refuses
any other, and :class:`DeclaredParameters` refuses a predicate that reads a parameter its
registration does not list. So the list is exactly what the predicate reads. Without these checks, a
rule that declared a parameter its predicate ignored would load and run as though the parameter were
absent, while its sentence said the parameter was in force.

A declared predicate need not be implemented yet. The registry distinguishes registered, enabled and
available. A predicate whose evaluation needs records that the engine does not yet produce is
registered and unavailable, with the reason recorded. That is the same shape as an extractor whose
optional dependency is missing. It lets the Rulebook be written before the inference layer exists,
without anybody pretending that the rule can run.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ingence.core.registry import Registration, Registry
from ingence.core.schema import FailureClass, RuleEffect
from ingence.core.tiers import ResolutionStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from ingence.core.relations import ExpansionState

__all__ = [
    "BUILT_IN_INDEXES",
    "CO_MENTION_INDEX",
    "ENTRY_SHAPED",
    "MENTION_INDEX",
    "PARENT_INDEX",
    "PREDICATES",
    "READS_BY_EFFECT",
    "TEMPORAL_FAMILIES",
    "Abstention",
    "ClassKey",
    "Concluded",
    "DeclaredParameters",
    "DirectoryTotals",
    "DrawsFromTheClassKey",
    "Embedding",
    "PairsWithinTheClass",
    "Predicate",
    "PredicateNotEvaluableError",
    "Raised",
    "RanksPairEnds",
    "Reach",
    "Reads",
    "Refusal",
    "UndeclaredParameterError",
    "VocabularyRead",
    "declare",
    "effect_of",
    "families_of",
    "parameters_of",
    "reach_declared",
    "reads_name_slots",
    "reads_no_name",
    "reads_of",
    "vocabulary_of",
]

#: Evidence families that describe *when* things happened rather than *what*
#: they are. A rule that proposes an edge may not score on any of them.
#:
#: The prohibition is on positive support, not on the evidence. A burst may bound candidate
#: generation, which is a constraint and narrows what is considered, and it may be displayed, which
#: is presentation. What it may never do is make a proposed edge more credible, because every
#: laboratory has months where three unconnected efforts were simultaneously active and all three
#: overlap perfectly.
#:
#: Named as families rather than as field names because
#: :class:`~ingence.core.schema.SupportComponent` already groups components that
#: are views of one fact, and the family is the level the prohibition belongs at:
#: forbidding one component name would leave the next spelling of the same fact
#: permitted.
TEMPORAL_FAMILIES: frozenset[str] = frozenset(
    {
        "burst",
        "window",
        "temporal_proximity",
        "clock_agreement",
    }
)


class Reach(StrEnum):
    """What one predicate reads, as an inference streamed by folder needs to know it.

    The classes are folder-local, cross-folder and the scan's own.

    - Folder-local: ``entry`` is one entry's own records. ``folder`` is one folder's direct
      children, the folder itself and what lies beside it.
    - Cross-folder: ``attached`` is an entry beside a value that the pass computes over the whole
      pool first. The others are ``root``, ``pool``, ``pair`` (two entries drawn within one class of
      an index) and ``group`` (a class of a grouping, which no phase assembles).
    - The scan's own: ``scan``.

    A predicate declares its reach where it is registered (:func:`declare`) or in
    :data:`ingence.infer.streaming.REACH`. A rule whose predicate declares neither runs whole where
    the pass would have streamed.
    """

    ENTRY = "entry"
    ATTACHED = "attached"
    FOLDER = "folder"
    ROOT = "root"
    POOL = "pool"
    PAIR = "pair"
    GROUP = "group"
    SCAN = "scan"


class Reads(StrEnum):
    """What a predicate is handed, declared on its registration.

    The first seven are one entry's records, a part of them, the entry beside its root, or its name
    beside the Dictionary's spellings. These are what a classifying or grouping rule is handed. The
    next four are the shapes that a proposing, constraining or flagging rule reads. The last is what
    the scan layer hands its own rules.
    """

    #: The entry itself: its filesystem facts.
    ENTRY = "entry"
    #: The components its name was cut into.
    COMPONENTS = "components"
    #: What the Dictionary made of each of those components.
    BINDINGS = "bindings"
    #: What was read from its content and its structure.
    OBSERVATIONS = "observations"
    #: All of those together, with the claims earlier phases made about it.
    RECORDS = "records"
    #: The entry beside what was read from its content. A rule uses it when its reading of an
    #: observation depends on the entry the observation lies on, as a digest read at another path
    #: does where the file has moved and no dive has checked the move.
    ENTRY_OBSERVATIONS = "entry_observations"
    #: The entry beside the record of the root it was found under: its filesystem facts,
    #: and whether that root's filesystem ignores case. A shape of its own, so a rule
    #: comparing observed metadata is never read as a rule about a name.
    ENTRY_IN_ROOT = "entry_in_root"
    #: The entry beside every spelling that the Dictionary gives each sense. A rule uses it to read
    #: what a whole name, or the start or end of one, spells. The pass hands the spellings over, so
    #: the predicate never reaches the Dictionary, and the rule names senses and not spellings.
    NAME_SPELLINGS = "name_spellings"
    #: The entry's components and bindings beside the origin that the Dictionary declares for each
    #: spelling of a format. The origin says who can have written a file of that spelling: an
    #: instrument as it acquires, or software afterwards. The pass hands it over as it hands
    #: ``NAME_SPELLINGS``, so the predicate never reaches the Dictionary, and the rule names senses
    #: and writers and not spellings.
    FORMAT_ORIGIN = "format_origin"
    #: The entry beside what its neighbors hold: the names, but for their numbers, of the files that
    #: hold bytes in its own folder and in the folders beside it. Proximity is what says an empty
    #: file is a real, empty output and not a stand-in for something held elsewhere. A predicate
    #: that reads it is never handed the neighbors themselves.
    ENTRY_AMONG_SIBLINGS = "entry_among_siblings"
    #: A folder beside what it holds: how many of the files in it and beneath it are an instrument's
    #: acquisitions and how many are named for one, and how far that material is shared with its
    #: parent's files and its siblings'. The pass counts these facts about names and formats, so a
    #: rule can say where one experiment's material is bound together without being handed another
    #: folder's records.
    FOLDER_CONTENTS = "folder_contents"
    #: The entry beside the group of experiments that a person approved and that holds it. This is
    #: the name of the proposed group whose member experiment's folder lies above the entry, where
    #: the Record says a person approved that group. Approval at the experiments' level is inherited
    #: by everything beneath them.
    APPROVED_GROUP = "approved_group"
    #: The entry beside the real file that an archive's link to it names. For a link that an archive
    #: left, this is the file elsewhere in the pool of its name (and of its path, where the link's
    #: text says one). For that file, it is the file itself. The pass matches from names and link
    #: text alone.
    ARCHIVED_COPY = "archived_copy"
    #: The entry beside the dates its own name holds and those of the nearest folder above it whose
    #: name holds one. A name's date is a neighborhood. It is descriptive and never a clock.
    NAME_DATES = "name_dates"
    #: The entry beside the acquisition its name holds whole, nearest it in its experiment, or, for
    #: an acquisition, itself. The pass matches from names and the classifying phase's acquisition
    #: claims, at the smallest minimum that any rule reading it names.
    NAMED_ACQUISITION = "named_acquisition"
    #: The entry beside the derivations proposed into it, each with its source's stages. It is read
    #: after the proposing phase, by a rule that asks whether a derivation and the stage that a name
    #: or a format states agree.
    DERIVED_FROM = "derived_from"
    #: The entry beside the set of copies that a person answered it belongs to. It is read from the
    #: Record at the run's watermark. The set is named by the class that a person answered "Copies"
    #: about. The pass assembles it after the rules that form copies have claimed their classes.
    ANSWERED_COPIES = "answered_copies"
    #: Two entries drawn from one class of an index, and never from the pool.
    PAIR = "pair"
    #: The candidates generated for one subject.
    CANDIDATES = "candidates"
    #: One class of a grouping, with its members.
    GROUP = "group"
    #: A folder or a root, with what it holds.
    SCOPE = "scope"
    #: What enumeration found, or what opening a container recovered, in the form that each of the
    #: scan layer's own rules reads it. The census and the cascade hand it over, and the inference
    #: pass never does.
    ENUMERATION = "enumeration"


class VocabularyRead(StrEnum):
    """What a predicate asks the pass to hand it from the Dictionary, beside its subject.

    The ask is declared on the registration, as what the predicate reads is. So the pass consults
    the Dictionary only where a rule in force needs it, and a predicate never reaches the Dictionary
    itself. It is handed the part that it declared, as a read-only value.
    """

    #: The programs and packages the vocabulary declares by the parts each lays out side by side
    #: (:mod:`ingence.dictionary.programs`), handed to a folder scope.
    PROGRAM_LAYOUTS = "program_layouts"
    #: Every spelling of each shipped domain pack that the project does not load, by domain. It is
    #: handed to a root scope so that a rule can count what a pack would read.
    UNLOADED_PACKS = "unloaded_packs"


#: What a subject built from one entry's records can be narrowed to.
ENTRY_SHAPED: frozenset[Reads] = frozenset(
    {
        Reads.ENTRY,
        Reads.COMPONENTS,
        Reads.BINDINGS,
        Reads.OBSERVATIONS,
        Reads.RECORDS,
        Reads.ENTRY_OBSERVATIONS,
        Reads.ENTRY_IN_ROOT,
        Reads.NAME_SPELLINGS,
        Reads.FORMAT_ORIGIN,
        Reads.ENTRY_AMONG_SIBLINGS,
        Reads.FOLDER_CONTENTS,
        Reads.APPROVED_GROUP,
        Reads.ARCHIVED_COPY,
        Reads.NAME_DATES,
        Reads.NAMED_ACQUISITION,
        Reads.DERIVED_FROM,
        Reads.ANSWERED_COPIES,
    }
)

#: What each effect can hand a predicate.
#:
#: A rule whose predicate reads anything else is refused at load, because it could only ever abstain
#: on what it was handed, and a predicate that always abstains looks exactly like one that found
#: nothing. Classifying and grouping run before the derivations exist, so neither may read them, for
#: that reason.
READS_BY_EFFECT: dict[RuleEffect, frozenset[Reads]] = {
    RuleEffect.CLASSIFIES: ENTRY_SHAPED - {Reads.DERIVED_FROM},
    RuleEffect.GROUPS: ENTRY_SHAPED - {Reads.DERIVED_FROM},
    RuleEffect.PROPOSES: frozenset({Reads.PAIR, Reads.GROUP}),
    RuleEffect.CONSTRAINT: frozenset({Reads.PAIR, Reads.CANDIDATES}),
    RuleEffect.FLAGS: ENTRY_SHAPED | {Reads.GROUP, Reads.SCOPE},
    RuleEffect.ENUMERATES: frozenset({Reads.ENUMERATION}),
}

#: The index of the files directly inside one folder. A proposing rule may draw its candidate pairs
#: within it, and no revision declares it. The loader, the predicates and the pass all read this one
#: spelling.
PARENT_INDEX = "parent"

#: The pairs that a file's own mentions resolve to: the mentioning file and each entry that one of
#: its mentions names. The pairs are directed, because a mention is: the source mentions the target.
#: They are drawn from reference resolution and not from any grouping, so no revision declares them.
MENTION_INDEX = "mentioned"

#: The entries one file's mentions resolve to, taken together: two files a describing file
#: names both of, which is where a derivation declared by a script or a workspace can be
#: proposed. Each pair still needs its direction from somewhere else.
CO_MENTION_INDEX = "co_mentioned"

#: The indexes no revision declares, because the pass draws them itself.
BUILT_IN_INDEXES = frozenset({PARENT_INDEX, MENTION_INDEX, CO_MENTION_INDEX})


class PredicateNotEvaluableError(RuntimeError):
    """A predicate was asked to evaluate before the records it reads exist.

    Distinct from a predicate being absent. Absent means somebody deleted it and
    a rule still names it, which the loader catches. This means the Rulebook is
    ahead of the inference layer, which is expected during the build and must not
    look like a rule failing: a rule that cannot be evaluated has produced no
    conclusion, which is a different state from a rule that produced none.
    """


class UndeclaredParameterError(LookupError):
    """A predicate read a parameter that its registration does not declare.

    This is a defect in a predicate or its registration and never in a rule, because the loader has
    already refused every rule that declares a parameter its predicate does not read. The error is
    raised on the read, so the defect cannot pass for a default.
    """


@dataclass(frozen=True, slots=True)
class Abstention:
    """Why a predicate reached no verdict on a subject it was handed.

    This is an absence state and the failure class that routes a remedy, so that a starved rule and
    a wrong rule do not look alike. An input that nothing produced is not a rule failing. An input
    that is present and unreadable is a coverage problem, and the remedy is vocabulary or an
    extractor. An input that points more than one way is the rule unable to decide, which is the
    only thing the interpretation class means.
    """

    status: ResolutionStatus = ResolutionStatus.EXAMINED_NO_EVIDENCE
    failure_class: FailureClass | None = None
    #: The records the abstention rests on, where some did.
    inputs: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        """Refuse an abstention that claims a verdict, or a failure a rule cannot have."""
        if self.status is ResolutionStatus.RESOLVED:
            message = "an abstention reached no verdict and cannot record one"
            raise ValueError(message)
        if self.failure_class is FailureClass.ACCESS:
            message = "a rule never fails to reach a file; access is the scan layer's failure"
            raise ValueError(message)
        conflicting = self.status is ResolutionStatus.EXAMINED_CONFLICTING
        if conflicting != (self.failure_class is FailureClass.INTERPRETATION):
            message = (
                "an input pointing more than one way is an interpretation failure, "
                "and nothing else is"
            )
            raise ValueError(message)
        if (
            self.failure_class is FailureClass.COMPREHENSION
            and self.status is not ResolutionStatus.EXAMINED_NO_EVIDENCE
        ):
            message = "a comprehension failure is an input that was examined and could not be read"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ClassKey:
    """The class a grouping predicate places a subject in.

    A grouping predicate returns a key and never whether two subjects stand in the relation. Two
    subjects are equivalent in the rule's respect exactly where their keys are equal. The kernel of
    a function is an equivalence relation, so reflexivity, symmetry and transitivity within the
    respect hold by construction, and no pair is ever compared. A subject that the rule has nothing
    to say about has no key and joins no class. That is how "absence on both sides is not agreement"
    survives.

    The key is what a member's claim names, so it is readable and canonical. Two subjects that agree
    in the respect produce the same string, whatever order their records arrived in.
    """

    key: str
    #: The records the key was computed from, which a member's claim lists as its
    #: inputs. A key is built from several records, so unlike a classified value it
    #: cannot be found among them by matching.
    inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse an empty key, which would put every subject returning one in one class."""
        if not self.key:
            message = "a class key must be non-empty; a subject in no class returns None"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a constraint refuses one candidate pair.

    A constraint gives a refusal and never a penalty. It carries a reason that a person can read and
    the records that the constraint read, and nothing that a decoder could weigh against support. A
    constraint with nothing to refuse returns None. That does not mean the pair holds. It means the
    constraint has nothing to say.
    """

    reason: str
    #: The records the refusal rests on, from either end of the pair.
    records: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a refusal that does not say why."""
        if not self.reason:
            message = "a refusal must say why, or it cannot be disputed"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Raised:
    """What a flagging predicate raises about one subject, and the records it rests on.

    A flag concludes nothing, so this is not a value that the subject has. It is what drew
    attention: a token standing between senses, or a doubt term in a name. What was raised often
    cannot be found among the handed records by matching, because the record that raised it may hold
    no resolved term at all. So the records travel with it, as they do on :class:`ClassKey`.
    """

    values: tuple[str, ...]
    #: The records the finding rests on, each one the subject's own.
    inputs: tuple[str, ...] = ()
    #: What the predicate says about it, for a person reading the finding.
    reason: str = ""

    def __post_init__(self) -> None:
        """Refuse a finding that raises nothing, which is an abstention and returns None."""
        if not self.values or not all(isinstance(value, str) and value for value in self.values):
            message = "a raised finding names what it raised; a subject raising nothing is None"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Concluded:
    """What a classifying predicate concludes where no handed record carries the value.

    The result holds the class that a rule names, and the records that licensed it. A
    classification's inputs are otherwise found by matching the concluded value against the handed
    records. That works where the value is a term that a binding resolved to. A rule that concludes
    a stage from a format binds no stage, so the records travel with the value, as they do on
    :class:`Raised`, and must be the subject's own.
    """

    values: tuple[str, ...]
    #: The records the conclusion rests on, each one the subject's own.
    inputs: tuple[str, ...]

    def __post_init__(self) -> None:
        """Refuse a conclusion that names nothing, or rests on nothing."""
        if not self.values or not all(isinstance(value, str) and value for value in self.values):
            message = "a conclusion names what it concludes; a subject concluding nothing is None"
            raise ValueError(message)
        if not self.inputs:
            message = "a conclusion no handed record carries must name the records it rests on"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class DirectoryTotals:
    """What one directory holds, directly and beneath it, as enumeration counted it.

    The census's closed-world statements about a directory are read off this verdict, so the
    arithmetic behind them belongs to the rule that licenses them.
    """

    direct_file_count: int
    direct_directory_count: int
    recursive_file_count: int
    recursive_directory_count: int
    recursive_size_bytes: int


@dataclass(frozen=True, slots=True)
class Embedding:
    """How a container holds one member recovered from it.

    The result holds the narrower relation that the catalog names for that kind of container, where
    it names one, and why expansion stopped, which every ``embeds`` edge must carry.
    """

    specialization: str | None
    expansion_state: ExpansionState


class DeclaredParameters(Mapping[str, object]):
    """A rule's parameters in force, readable only where its predicate declares them.

    The loader holds a rule to its predicate's declaration, and this class holds the predicate to
    it. A rule that declares a parameter its predicate does not read is refused at load. A predicate
    that reads a parameter its registration does not list is refused here, on the read, whether or
    not the rule declared it. Read through ``get``, it would otherwise take a default that nobody
    declared, and the registration would stop saying what the predicate reads.
    """

    __slots__ = ("_declared", "_predicate", "_values")

    def __init__(
        self, values: Mapping[str, object], *, predicate: str, declared: frozenset[str]
    ) -> None:
        self._values = dict(values)
        self._predicate = predicate
        self._declared = declared

    def __getitem__(self, name: str) -> object:
        return self._values[self._declared_name(name)]

    def get(self, name: str, default: object = None) -> object:
        """Return a declared parameter's value, or the default where the rule leaves it out."""
        return self._values.get(self._declared_name(name), default)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self._declared_name(name) in self._values

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def _declared_name(self, name: str) -> str:
        """Return a name the predicate declares it reads, refusing any other."""
        if name not in self._declared:
            message = (
                f"predicate {self._predicate!r} read the parameter {name!r}, which its "
                f"registration does not declare"
            )
            raise UndeclaredParameterError(message)
        return name


@runtime_checkable
class Predicate(Protocol):
    """What every rule predicate implements.

    Deliberately small, for the reason the extractor contract is: an interface
    with many methods is one a new predicate cannot be written against without
    reading an existing implementation.
    """

    #: Registry name. Also what a rule's ``predicate`` field holds, and what
    #: appears on every claim the rule licenses.
    name: str
    #: Bumped whenever the predicate's verdict could change for unchanged input.
    #: Enters the effective-rulebook hash, so a change re-decides what it affects.
    version: str

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Decide whether this predicate holds of one subject.

        The subject is what the registration says the predicate reads, narrowed from what the rule's
        effect hands over. A classifying or grouping rule gets one entry's records or part of them.
        A proposing or constraining rule gets a pair. See :mod:`ingence.infer.subjects`.

        Returns a value, several values, or None to abstain. Raises
        :class:`PredicateNotEvaluableError` where the records it reads do not exist yet. Must not
        raise for an ordinary negative: not holding is a verdict, and abstaining is a third verdict
        distinct from both.

        A grouping predicate returns a :class:`ClassKey` and not a value. The key is the class that
        its subject belongs to, and never whether two subjects stand in the relation. A classifying
        predicate that concludes a value that no handed record carries returns a :class:`Concluded`,
        with the records it rests on.

        A predicate may also define ``explain_abstention(subject, parameters)`` that returns an
        :class:`Abstention`. It is optional and read with a default, because a predicate with
        nothing to say about why it abstained abstained on nothing.
        """
        ...


class RanksPairEnds(abc.ABC):
    """A proposing predicate whose every supported pair climbs an order its two ends hold alone.

    This is the cheap refusal before any pair is formed. On a large census, drawing within one
    grouping could form millions of pairs and propose none, because no pair held an earlier stage
    and a later one, and every pair was read end to end to find that out. ``end_rank`` reads one end
    on its own and returns its place in the order, or None where no pair holding it could be
    supported. ``least_rise`` says how far a supported pair's target stands above its source. The
    proposing phase ranks each member of a class once. It forms a pair only where both ends are
    ranked and the target stands at least that far above the source, so a class whose members hold
    no such two forms nothing.

    This is a contract and not a heuristic. For every pair that the predicate would support, both
    ends are ranked and the rise holds. A pair left unformed is one that the predicate would have
    refused, so the candidates are the same with the drawing bounded or not, and declaring it leaves
    the predicate's version unchanged. The declaration is optional. A predicate that does not
    declare it has every pair that its index offers formed.

    A predicate declares this by inheriting this class and never by the names of its methods. A
    predicate whose ranks are wrong loses candidates without a trace in any table. So a predicate
    that happens to have callables of these names, or a mock that stands in for one, must never be
    ranked by accident.
    """

    @abc.abstractmethod
    def end_rank(self, records: object, parameters: Mapping[str, object]) -> int | None:
        """One end's place in the order, read from the end alone; None where it holds none."""

    @abc.abstractmethod
    def least_rise(self, parameters: Mapping[str, object]) -> int:
        """How far a supported pair's target stands above its source in that order: 0 or more."""


class DrawsFromTheClassKey(abc.ABC):
    """A proposing predicate whose pairs run from the entry that a class's key names to each member.

    A pair is formed only where a key joins its ends, looked up. A class of files named for one
    acquisition holds the acquisition and every file whose name holds it whole. The name joins each
    file to the acquisition, and two files named for one acquisition to nothing. So the drawing
    forms, for each member but the one that the key names, the one pair from that entry to it, with
    the member as the subject, whose one counterpart is what its key names. A class whose key names
    no member forms nothing, and no class forms its square.

    A predicate declares this by inheriting this class and never by the name of a method, as with
    :class:`RanksPairEnds`. A predicate drawn this way loses every pair but the key's, so it must
    never be drawn so by accident.
    """

    @abc.abstractmethod
    def key_source(self, key: str) -> str | None:
        """The entry a class's key names, which is every pair's source; None where it names none."""

    def sources(
        self, key: str, members: Sequence[object], parameters: Mapping[str, object]
    ) -> dict[str, str]:
        """Each member's one source, by its identifier: the entry the key names, by default.

        The function is read once per class. A predicate may place a member after another member
        instead, for example a chain made link by link. Every source must be a member of the class,
        and no member is its own.
        """
        del parameters
        source = self.key_source(key)
        identifiers = {
            getattr(getattr(member, "entry", None), "entry_id", None) for member in members
        }
        if source is None or source not in identifiers:
            return {}
        return {
            str(identifier): source
            for identifier in sorted(i for i in identifiers if i is not None)
            if identifier != source
        }


class PairsWithinTheClass(abc.ABC):
    """A proposing predicate whose class's key says which of its members are paired.

    A pair is formed only where a key joins its ends, looked up, as :class:`DrawsFromTheClassKey`
    has it, where a key names more than one end. The class of a series of numbered files and the
    results table named for it pairs each file with the table. The table is every pair's target, and
    no pair joins two files of the series. The class of one name in sibling folders pairs the file
    in a folder whose words say it holds sources with the one in a folder whose words say it holds
    products. Each source is a subject whose counterparts are its targets. So a bound on a subject's
    candidates cuts only where one file is the source of more targets than the bound allows, and
    never because its class is large.

    A predicate declares this by inheriting this class and never by the name of a method, on the
    precedent of the two above. A predicate drawn so loses every pair that its key does not name.
    """

    @abc.abstractmethod
    def pairs_within(
        self, key: str, members: Sequence[object], parameters: Mapping[str, object]
    ) -> Mapping[str, tuple[str, ...]]:
        """Each source member's targets, by identifier, every one a member and none its own."""


#: Every rule predicate, reachable only through here.
#:
#: A registration declares the effect class it serves, what it reads and the evidence families it
#: may contribute, as attributes, so the loader can check a rule against its predicate without
#: constructing the predicate. That matters because a predicate whose records do not exist yet is
#: registered and unavailable, and a loader that had to build one in order to validate a rule could
#: not validate that rule at all.
PREDICATES: Registry[Predicate] = Registry("predicate")


def declare(
    name: str,
    *,
    version: str,
    summary: str,
    effect: RuleEffect,
    reads: Reads,
    support_families: tuple[str, ...] = (),
    parameters: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
    enabled: bool = True,
    vocabulary: tuple[VocabularyRead, ...] = (),
    reads_name_slots: bool = False,
    reads_no_name: bool = False,
    reach: Reach | str = "",
) -> Callable[[Callable[[], Predicate]], Callable[[], Predicate]]:
    """Register a predicate, stating its effect, what it reads, its parameters and families once.

    A thin wrapper over :meth:`Registry.register` and worth having, because these
    must live in the registration rather than on the instance. The loader needs
    them to validate a rule, and a predicate that cannot be built yet is precisely
    the case where validation matters most: a second declaration on the class
    would be a second place for them to be wrong, and the one the loader could not
    read would be the one that drifted.

    Used as::

        @declare("stage_from_extension", version="1", summary="...",
                 effect=RuleEffect.CLASSIFIES, reads=Reads.BINDINGS)
        def _build() -> Predicate:
            from ingence.rulebook.predicates.classify import StageFromExtension

            return StageFromExtension()
    """
    return PREDICATES.register(
        name,
        version=version,
        summary=summary,
        requires=requires,
        enabled=enabled,
        attributes={
            "effect": effect.value,
            "reads": reads.value,
            "support_families": ",".join(sorted(support_families)),
            "parameters": ",".join(sorted(set(parameters))),
            "vocabulary": ",".join(sorted({read.value for read in vocabulary})),
            "name_slots": "yes" if reads_name_slots else "",
            "no_name": "yes" if reads_no_name else "",
            # What the predicate reaches, for an inference streamed by folder. The registration is
            # refused here if it names no reach, so a misspelling fails at import and not at a run.
            "reach": Reach(reach).value if reach else "",
        },
    )


def vocabulary_of(registration: Registration[Predicate]) -> frozenset[VocabularyRead]:
    """Return what a registered predicate asks to be handed from the Dictionary.

    The result is nothing for a registration that declares nothing.
    """
    declared = registration.attributes.get("vocabulary") or ""
    return frozenset(VocabularyRead(item) for item in declared.split(",") if item)


def reach_declared(registration: Registration[Predicate]) -> Reach | None:
    """Return the reach a predicate declared where it was registered, or None."""
    declared = registration.attributes.get("reach")
    return Reach(declared) if declared else None


def reads_name_slots(registration: Registration[Predicate]) -> bool:
    """Whether a registered predicate reads where a name's components stand.

    A name layout is one more table to read, so the pass loads it only where a rule in force
    declares it needs it. A predicate that reached for slots it did not declare would read none.
    """
    return bool(registration.attributes.get("name_slots"))


def reads_no_name(registration: Registration[Predicate]) -> bool:
    """Whether a registered predicate handed an entry's records whole reads nothing of its name.

    Its abstention is its own, and it is never "the name was not read". The predicate declares this,
    as ``reads_name_slots`` is declared. A predicate that declares nothing is taken to read names.
    """
    return bool(registration.attributes.get("no_name"))


def effect_of(registration: Registration[Predicate]) -> RuleEffect:
    """Return what effect class a registered predicate serves.

    Read off the registration rather than an instance, for the reason
    :func:`~ingence.executors.supervisor._version_of` reads a version off a declaration: the
    instance may be exactly the thing that could not be built.

    Refuses rather than defaulting. An effect decides which phase a rule runs in,
    and a default would put a rule in the wrong phase silently, where the symptom
    is an absent conclusion rather than an error.
    """
    declared = registration.attributes.get("effect")
    if not declared:
        message = (
            f"predicate {registration.name!r} declares no effect; a rule's effect "
            f"decides where in the pipeline it runs and cannot be defaulted"
        )
        raise ValueError(message)
    return RuleEffect(declared)


def reads_of(registration: Registration[Predicate]) -> Reads:
    """Return what a registered predicate reads.

    Refuses rather than defaulting, for :func:`effect_of`'s reason: a default
    would hand a predicate a subject it never asked for, and the symptom would be
    an abstention rather than an error.
    """
    declared = registration.attributes.get("reads")
    if not declared:
        message = (
            f"predicate {registration.name!r} declares nothing it reads; what a "
            f"predicate is handed cannot be defaulted"
        )
        raise ValueError(message)
    return Reads(declared)


def families_of(registration: Registration[Predicate]) -> tuple[str, ...]:
    """Return the evidence families a predicate may contribute support under.

    Empty where none is declared, which is the safe direction: a predicate
    contributing no support cannot violate a prohibition on which support it
    contributes, so the temporal check passes vacuously rather than failing closed
    on a predicate that scores nothing.
    """
    declared = registration.attributes.get("support_families", "")
    return tuple(family for family in declared.split(",") if family)


def parameters_of(registration: Registration[Predicate]) -> frozenset[str]:
    """Return the parameters a registered predicate reads.

    The result is empty where none is declared. That admits no parameter at all: a rule may not set
    what its predicate never reads, and a predicate that reads a parameter it does not declare is
    refused on the read.
    """
    declared = registration.attributes.get("parameters", "")
    return frozenset(name for name in declared.split(",") if name)
