"""The record contract: every record type, every field, every enum.

Everything the engine writes is one of the records declared here. Every record carries a schema
version, and no other module invents a field. This part of the system is expensive to change.

Four commitments are structural here. Each is visible in the shape of the models and does not
depend on discipline.

Observations and claims never share a container. :class:`Observation` and :class:`Claim` are
different models with no field into which either could be written. A fact and an inference that
can occupy one column will eventually be confused, and the system will then be unable to say which
it holds.

Absence is three states, not one. :class:`~ingence.core.tiers.ResolutionStatus` appears on every
entity. A null would collapse "not looked at", "looked at and found nothing" and "looked at and
found conflict" into one silence, which is how a coverage figure becomes a lie.

Evidence is kept whole. A claim carries an evidence vector of named, family-tagged components,
never a single number computed before the decision. The tier is the decision. A score orders
candidates within a tier and nothing else. No field in this module is a probability, and none is
named as if it were.

Support is positive only. A support component's value cannot be negative. Impossibility is a hard
constraint in the decoder, not a large negative weight, because a scoring language cannot express
impossibility and pretending otherwise lets accumulated weak support outvote a contradiction.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ingence import SCHEMA_VERSION
from ingence.core.ids import (
    ID_SCHEME_VERSION,
    CaseSensitivity,
    LocatorStep,
    Scope,
)
from ingence.core.outcomes import (
    CLOCK_TIER_OF,
    CLOCK_TIERS,
    NON_REUSABLE_OUTCOMES,
    ClockTier,
    ClockTierProperties,
    IdentityScope,
    ScanOutcome,
    SourceScope,
    TimeSource,
    ValueKind,
)
from ingence.core.relations import (
    PRIMITIVE_PROPERTIES,
    AllenRelation,
    ExpansionState,
    Primitive,
    QuotientOrigin,
    ReferenceResolution,
)
from ingence.core.tiers import (
    DecisionTier,
    ExtractionTier,
    InferenceTier,
    ResolutionStatus,
)

__all__ = [
    "CLOCK_TIERS",
    "CLOCK_TIER_OF",
    "NON_REUSABLE_OUTCOMES",
    "UNCHECKED_MOVES",
    "WHOLE_DIGEST_UNCHECKED_MOVES",
    "AreaKind",
    "Assertion",
    "AssumptionKind",
    "Candidate",
    "CaseReadingStated",
    "Claim",
    "ClockKind",
    "ClockTier",
    "ClockTierProperties",
    "Component",
    "ComponentActivation",
    "DecisionStatus",
    "Defeater",
    "DictionaryEntry",
    "DictionaryOrigin",
    "DictionaryStatus",
    "Entry",
    "EntryKind",
    "EntryType",
    "EquivalenceLevel",
    "ExcludedNames",
    "ExclusionReason",
    "FailureClass",
    "Finding",
    "FindingSubject",
    "IdentityScope",
    "InvariantOutcome",
    "JournalEvent",
    "MentionTally",
    "MovedBy",
    "Observation",
    "PackInForce",
    "ParameterSpec",
    "ParameterType",
    "PlatformRecord",
    "QualityFigure",
    "QualityState",
    "RecognisedAs",
    "RecognisedPath",
    "Record",
    "RecordEvent",
    "RecordEventKind",
    "ResolutionTally",
    "ReuseDecision",
    "Role",
    "Root",
    "Rule",
    "RuleComposition",
    "RuleEffect",
    "RuleFailure",
    "RuleStatus",
    "RunRecord",
    "Salience",
    "ScanError",
    "ScanOutcome",
    "SourceScope",
    "SupportComponent",
    "Theme",
    "ThemeKind",
    "TimeSource",
    "ValueKind",
    "WarningTally",
]


# ---------------------------------------------------------------------------
# Shared vocabulary.
# ---------------------------------------------------------------------------


class EntryKind(StrEnum):
    """Whether an entry is a filesystem object or a promoted derived stream."""

    #: An ordinary file, directory or symlink. Almost everything.
    FILESYSTEM = "filesystem"
    #: A stream recovered from inside a file and promoted to a node of its own.
    #: Promotion is an event, not a default: a derived stream becomes an entry
    #: only when a rule or a person needs it to be an endpoint of an edge.
    DERIVED = "derived"


class EntryType(StrEnum):
    """What kind of filesystem object an entry is."""

    FILE = "file"
    DIRECTORY = "directory"
    #: Recorded, never traversed. Following symlinks turns a tree into a graph
    #: and can turn a scan into an infinite walk.
    SYMLINK = "symlink"
    #: Sockets, pipes, devices. Recorded so the census is complete.
    OTHER = "other"


class SymlinkState(StrEnum):
    """Whether a symlink's target is there, which is not the same as whether the link is.

    The engine resolves a link and never traverses it. Stating that a target exists costs one
    ``stat``. Walking into it turns the containment forest into a graph and can turn a scan into
    an infinite one, which is why :attr:`ScanSettings.follow_symlinks` is frozen shut.

    The engine keeps these states apart for a link as it does everywhere. A link whose target is
    gone and a link the engine could not ask about are different states, and both differ from a
    link that resolves. A stub standing in for a file that exists elsewhere is somebody's data. A
    stub standing for nothing is not, and only the first is worth a person's attention.
    """

    #: Not a symlink. Every ordinary file and folder.
    NOT_A_LINK = "not_a_link"
    #: The target exists. What it is, and whether it is inside a root, are separate questions.
    RESOLVES = "resolves"
    #: The target does not exist. The link is real and what it points at is not.
    BROKEN = "broken"
    #: Could not be determined: permission refused, a loop, or a mount that did not answer.
    #: This is not the same as broken. Conflating them would assert an absence nobody observed.
    UNREADABLE = "unreadable"
    #: The target is written as a path outside the root the link was found under: another tree,
    #: another mount, or another machine's path (``\??\UNC\...``, ``C:\...``). The census reads
    #: this from the link's own text and never calls ``stat`` on it, because a census reads
    #: nothing outside the roots it was given. On a laboratory server this is how an archive
    #: leaves a file behind, and "broken" would assert an absence nobody observed. A link whose
    #: chain leaves the root at a later hop is this too.
    OUTSIDE_ROOT = "outside_root"


class MovedBy(StrEnum):
    """How a walk knew a file at a new path is a file an earlier census held elsewhere (1.22).

    A move is an observation about the filesystem between two censuses. It derives from both, so
    the Index can reproduce it. It is never a person's decision and never a claim. The values run
    from the strongest evidence to the weakest. A walk reads no content, so at walk time a match
    is usually by name, size and time. The next dive that reaches the file computes a digest, and
    an equal digest confirms the move.
    """

    #: The same whole-content digest on both sides: the same bytes.
    WHOLE_DIGEST = "whole_digest"
    #: The same sampled digest on both sides.
    SAMPLED_DIGEST = "sampled_digest"
    #: The same name, size and modification time, and the same file on the same volume. The
    #: filesystem's own number for the file (its inode) is unchanged on an unchanged device. A
    #: rename keeps it, and a copy, a clone included, never does.
    SAME_FILE = "same_file"
    #: The same name, size and modification time, and the same birth time. A rename keeps the
    #: moment a file was made and a copy does not. So where both sides carry a filesystem birth
    #: time that is not merely the modification time over again, equal birth times confirm the
    #: match.
    BIRTH_TIME = "birth_time"
    #: The same name, size and modification time, in another folder, and nothing else matching.
    NAME_SIZE_TIME = "name_size_time"
    #: The same name, size and modification time as more than one file gone, told apart by its
    #: neighbours. The files of the folder it left arrived together in one new folder, and this
    #: one is the match that folder's move names.
    NEIGHBOURS = "neighbours"
    #: A folder moved by its contents. Most of the files it held arrived together, one to one, in
    #: this new folder, whatever the folder is called now.
    CONTENTS = "contents"
    #: A copy whose original is gone. It has the same name, size and modification time, and the
    #: filesystem says it is another file: another file number on the same volume, or a later
    #: birth time. What was read and decided about the original follows the copy, as for any
    #: move, and the value says it was a copy.
    COPIED = "copied"
    #: A copy whose original is gone, its bytes confirmed. A dive's digest of the copy is the
    #: original's, and the move still says it was a copy.
    COPIED_SAME_BYTES = "copied_same_bytes"
    #: A folder moved by its contents, every one of which arrived as a copy. Each original is
    #: removed, as ``contents`` reads it, and the value says the move rests on copies alone. A
    #: folder made afresh from the same files as a removed one reads this way too.
    COPIED_CONTENTS = "copied_contents"
    #: Not moved after all. A walk matched the file without reading it, and a dive then found that
    #: its digest differs from the departed file's. ``moved_from`` still names the file it was
    #: taken for, so the undoing is visible. Nothing read or decided about the departed file
    #: follows it.
    DIGEST_DIFFERS = "digest_differs"


#: The moves that no dive has checked yet: those a walk matched without reading the file. A
#: digest's match is settled, ``digest_differs`` was refuted, and a folder's is by its contents.
#: The rest wait for a dive, and a later walk or rebind carries them until one comes.
UNCHECKED_MOVES: frozenset[MovedBy] = frozenset(
    {
        MovedBy.SAME_FILE,
        MovedBy.BIRTH_TIME,
        MovedBy.NAME_SIZE_TIME,
        MovedBy.NEIGHBOURS,
        MovedBy.COPIED,
    }
)

#: The moves after which a whole-file digest that a walk carried is still the departed file's.
#: These are the moves no dive has checked, and the one a dive checked by a sample alone, which
#: says nothing of the bytes between its ends. A digest a dive computed at the file's new path is
#: its own, whatever the move says. ``copied_same_bytes`` counts as checked, although it does not
#: say which digest confirmed it.
WHOLE_DIGEST_UNCHECKED_MOVES: frozenset[MovedBy] = UNCHECKED_MOVES | {MovedBy.SAMPLED_DIGEST}


class FailureClass(StrEnum):
    """What kind of thing failed, and therefore where the fix belongs.

    The class of a failure routes the remedy. Suppose a rule fails four times in five and the
    failures are predominantly comprehension failures. The rule needs no modification. It is being
    starved of inputs, and loosening it would compensate for missing data by degrading a rule that
    was correct. That is the most common way rule tuning makes a system worse, and classifying
    prevents it without anybody having to notice.

    There are three classes because three different people can fix a thing: whoever administers
    the machine, whoever writes extractors, and whoever writes rules.
    """

    #: Could not stat, could not open, permission denied, vanished mid-scan. A
    #: property of the environment. Fix mounts or permissions and re-run.
    #: Never touch a rule.
    ACCESS = "access"
    #: Opened, but no extractor supported it or the extractor errored. A
    #: property of coverage. Write an extractor, or add vocabulary.
    COMPREHENSION = "comprehension"
    #: Understood and unplaceable: no candidates, or conflicting ones. A property
    #: of the rules. Edit a rule, or ask the user. Recorded on a
    #: :class:`RuleFailure` when a rule cannot decide, and refused by a
    #: :class:`ScanError`.
    INTERPRETATION = "interpretation"


class ClockKind(StrEnum):
    """Which clock a time-consuming rule reads.

    Exposed as a rule parameter so a user can see and change which clock a
    conclusion rests on, rather than discovering it by reading code.
    """

    #: Time read from file content. Preferred where available.
    EVENT = "event"
    #: Time reported by the filesystem.
    RECORDED = "recorded"
    #: Event time where present, recorded time otherwise.
    EITHER = "either"


class DecisionStatus(StrEnum):
    """The standing of an assertion in the published graph."""

    #: Selected by the decoder and published.
    ASSERTED = "asserted"
    #: Rejected by a hard constraint. Kept with its reason, because a rejection
    #: a user cannot see is a rejection a user cannot dispute.
    BLOCKED = "blocked"
    #: Replaced by a later assertion. History is never removed.
    SUPERSEDED = "superseded"
    #: Withdrawn by a person through the Record.
    WITHDRAWN = "withdrawn"


class ReuseDecision(StrEnum):
    """Why a re-scan did or did not reuse a previous record.

    Counted into every run record so that "why was reuse lower than expected" is
    a query rather than an investigation. The order is the order the decision is
    made in.
    """

    #: Nothing was recorded for this entry before.
    NO_PREVIOUS_RECORD = "no_previous_record"
    #: The file itself changed: size, modification time, change time, or kind.
    STAT_CHANGED = "stat_changed"
    #: The work the engine would do today differs from the work it did before.
    #: This is the unusual half of the model and the one that stops an improved
    #: parser from being shadowed by results the old parser produced.
    PROCESSING_CHANGED = "processing_changed"
    #: The previous record does not satisfy what today's policy asks for. A
    #: metadata-only record cannot serve a run with extraction enabled, and an
    #: error or a deferral is never reusable: failure is not cached.
    PREVIOUS_INCOMPLETE = "previous_incomplete"
    #: Reused.
    REUSE = "reuse"


# ---------------------------------------------------------------------------
# The base record.
# ---------------------------------------------------------------------------


class Record(BaseModel):
    """Base of every persisted record.

    Frozen because a record, once written, is history. Extra fields are forbidden
    because a misspelled field name must fail loudly rather than be absorbed and
    lost.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)

    #: The version of this contract that produced the record. Every artifact carries it, because
    #: adding version discipline after a format is in the field is painful and sometimes
    #: impossible.
    schema_version: str = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Supporting structures.
# ---------------------------------------------------------------------------


class SupportComponent(Record):
    """One named piece of positive support for a candidate or a claim.

    Components are kept as a vector and never summed into a scalar before the
    decision is made. Summation is what triple-counts one underlying fact seen
    three ways, and it is why ``family`` exists: components sharing a family are
    views of one fact and must not compound with each other.
    """

    name: str
    #: Components sharing a family are not independent. Name similarity, name
    #: family match and fuzzy name support are three views of one fact and belong
    #: to one family.
    family: str
    #: Positive support only. Impossibility is a hard constraint in the decoder,
    #: never a negative weight, because a score cannot express impossibility and
    #: a large enough pile of weak support would otherwise outvote a
    #: contradiction.
    value: Annotated[float, Field(ge=0.0)]
    #: The observations this support was computed from, so any number can be
    #: walked back to the bytes it came from mechanically.
    observations: tuple[str, ...] = ()


class Defeater(Record):
    """A recorded reason a claim would be retracted.

    Defeasible and abductive claims must be able to name what would defeat them.
    A defeater is a visible event: evidence either raises a claim's tier, leaves
    it unchanged, or introduces one of these. It never quietly erodes a score.
    """

    reason: str
    rule_id: str
    rule_version: str
    #: The observations that constitute the defeat, where it has already fired.
    observations: tuple[str, ...] = ()
    #: True when the defeating evidence is present, false when this records only
    #: what *would* defeat the claim.
    fired: bool = False


class Salience(Record):
    """How much of a person's limited attention something deserves.

    Independent of confidence, and the two must not be collapsed. A byte-identical
    duplicate among ten thousand thumbnails is maximally confident and nearly
    worthless. A tentative link between an instrument export and a manuscript
    figure is low-confidence and extremely valuable.

    Four terms, kept separate so a ranking can be explained rather than only
    produced.
    """

    #: How uncommon this edge type or component value is in this project.
    rarity: Annotated[float, Field(ge=0.0)] = 0.0
    #: How many other pending decisions resolve if this one resolves.
    leverage: Annotated[float, Field(ge=0.0)] = 0.0
    #: Whether the endpoints bridge otherwise-disconnected parts of the graph.
    structural_position: Annotated[float, Field(ge=0.0)] = 0.0
    #: What a mistake here would corrupt. A misassigned raw acquisition corrupts
    #: a whole chain. A misassigned thumbnail does not.
    cost_of_error: Annotated[float, Field(ge=0.0)] = 0.0


class JournalEvent(Record):
    """One structured entry in the run journal.

    The journal logs activity at every stage, so that when part of the pipeline misbehaves nobody
    has to read the whole pipeline to find out where. It is written as structured rows and not as
    free text. Every question asked of such a log is a query: which stage was slow, what
    happened to this particular file, why the reuse rate dropped between two runs.

    The journal is exhaustive by default. The setting ``journal.enabled`` switches it off.
    """

    journal_id: str
    run_id: str
    #: Monotonic within a run. Gives a total order even when several events
    #: share a timestamp, which at these rates they routinely do.
    sequence: int
    occurred_at: str
    #: Nanoseconds since the run started. A wall clock can step backwards. This
    #: cannot, so durations computed from it are trustworthy.
    elapsed_ns: int

    #: Which part of the engine: census, deepen, infer, commit, compact.
    stage: str
    #: What happened: started, finished, reused, examined, skipped, error,
    #: flushed, canceled.
    event: str
    #: The thread or process that emitted it, so contention is visible.
    worker: str = ""

    entry_id: str | None = None
    relative_path: str | None = None
    #: How long the unit took, where the event marks a completion.
    duration_ns: int | None = None
    #: Canonically serialized detail. Deliberately unconstrained: the journal
    #: records whatever a stage knows, and constraining it here would mean a
    #: schema change every time a stage learned something new.
    detail: str = "{}"


class ScanError(Record):
    """An error encountered while reading, retained as data rather than raised.

    Errors are observations about the filesystem. A scan that discards them
    reports a smaller, cleaner and less true picture than the one it found.
    """

    entry_id: str | None
    relative_path: str
    operation: str
    error_type: str
    message: str
    run_id: str
    #: What kind of failure this was, and therefore whose problem it is. The default is access.
    #: The scan layer is where most failures arise, and an unclassified failure that silently
    #: reads as a rule problem is the one mistake the partition exists to prevent.
    failure_class: FailureClass = FailureClass.ACCESS

    @model_validator(mode="after")
    def _the_scan_layer_never_produces_an_interpretation_failure(self) -> ScanError:
        """Nothing that reads bytes can fail to interpret them.

        Interpretation failure is a property of the rules, and a rule that could
        not decide is recorded on a :class:`RuleFailure`. Refusing it here
        means the class cannot be populated by the wrong layer, and
        :class:`RuleFailure` refuses the other two in turn.
        """
        if self.failure_class is FailureClass.INTERPRETATION:
            message = (
                "a scan error is never an interpretation failure: interpretation "
                "is a property of the rules, which this layer does not run"
            )
            raise ValueError(message)
        return self


class RuleFailure(Record):
    """A rule unable to decide on one subject, recorded against the rule.

    This is the only record that carries the interpretation class. Failure is partitioned by who
    can fix it. Access belongs to the environment and comprehension to coverage, and the scan
    layer records both on a :class:`ScanError`. Interpretation belongs to the rules, and is
    recorded here.

    A rule failure is a record of its own and not a claim with a conflicting status. A claim is a
    conclusion the engine derived, and a rule that could not decide derived none. A table of
    claims holding non-conclusions is a table every reader has to filter before believing
    anything in it.

    The record refuses the other two classes, the mirror of :class:`ScanError` refusing this one.
    The partition then holds from both sides, and a starved rule and a rule unable to decide
    cannot be recorded as each other.
    """

    failure_id: str
    run_id: str
    rule_id: str
    rule_version: str
    scope: Scope
    #: The entry the rule could not decide on.
    subject: str
    failure_class: FailureClass = FailureClass.INTERPRETATION
    #: The records pointing more than one way, so the failure walks back to them.
    inputs: tuple[str, ...] = ()
    reason: str = ""

    @model_validator(mode="after")
    def _only_a_rule_fails_to_interpret(self) -> RuleFailure:
        """A rule never fails to reach a file or to read one. Those are the scan layer's."""
        if self.failure_class is not FailureClass.INTERPRETATION:
            message = (
                f"a rule failure is an interpretation failure, and "
                f"{self.failure_class.value} is the scan layer's, recorded on a scan error"
            )
            raise ValueError(message)
        return self


class FindingSubject(StrEnum):
    """What a raised finding is about."""

    #: One entry, raised for what its own records hold.
    ENTRY = "entry"
    #: A class a grouping rule formed. Declared: no phase hands a flagging rule one yet.
    GROUP = "group"
    #: A region of the pool and every entry it holds, raised for something no one file
    #: carries: a rate, or a label recurring across folders. One indexed root today.
    SCOPE = "scope"


class Finding(Record):
    """What a flagging rule raised about one subject, for a person's attention.

    A finding is a record of its own and never a claim. A flagging rule concludes nothing (see
    ``rulebook/predicates/flag.py``), and a claim is a conclusion. So a finding holds no object
    the subject is said to be, no tier, no status and no defeaters. It names what drew attention
    and the records that did, and what that means is a person's to say.

    A finding is not an observation either. An observation is what an extractor read off the
    bytes, with a locator and a source scope, and a run carries it forward. A finding is what a
    rule in the Rulebook raised from records the Index already holds, attributed to that rule's
    version and scope. Every inference computes findings again, so a rule switched off raises
    nothing on the next run.

    A finding has no salience field. Ranking findings is a record and a job of its own, and a
    finding's identifier is what such a record would cite.
    """

    finding_id: str
    run_id: str
    rule_id: str
    rule_version: str
    scope: Scope
    #: What the finding is about: an entry identifier, or a scope's token. A group once a
    #: phase hands a flagging rule one.
    subject: str
    #: What drew attention, in canonical order: a token standing between senses, a doubt term
    #: in a name, a content type its name contradicts.
    raised: tuple[str, ...]
    subject_kind: FindingSubject = FindingSubject.ENTRY
    #: The subject's own records the finding rests on, so it walks back to them.
    inputs: tuple[str, ...] = ()
    #: What the predicate said about it, where it said something.
    reason: str = ""
    #: What the flagging rule names as a reason its finding may not deserve a person's
    #: attention, evaluated on the subject, fired or not.
    #:
    #: A defeater is recorded and is never a reason to hide the finding. A flag still concludes
    #: nothing and still raises. A fired defeater says the engine also found a reason to look
    #: twice. An example is an empty file inside a folder laid out as an installed program, where
    #: an empty file is ordinary. The Review queue counts them. A finding written before schema 1.15
    #: carries none.
    defeaters: tuple[Defeater, ...] = ()

    @model_validator(mode="after")
    def _raises_something(self) -> Finding:
        """A finding raising nothing is an abstention, which is not recorded."""
        if not self.raised or not all(self.raised):
            message = "a finding names what it raised; a subject raising nothing has no finding"
            raise ValueError(message)
        return self


class PlatformRecord(Record):
    """The machine a run happened on.

    Recorded because several fields in :class:`Entry` are platform-dependent and
    several time semantics differ between operating systems. A result that cannot
    be attributed to a platform cannot be compared with one from another.
    """

    system: str
    release: str
    machine: str
    python_version: str
    #: Filesystem case sensitivity observed at the root, where determined.
    case_sensitivity: CaseSensitivity = CaseSensitivity.UNKNOWN


class ComponentActivation(Record):
    """One optional component's state during a run.

    Produced by :meth:`ingence.core.registry.Registry.snapshot`. Every run states
    which optional components were configured, which were enabled, and which
    could actually run, so a measured result is attributable to a configuration.
    """

    kind: str
    name: str
    version: str
    enabled: bool
    available: bool


class InvariantOutcome(Record):
    """The result of one publication invariant on one run."""

    invariant_id: str
    statement: str
    passed: bool
    #: How many violations were found. Zero when the invariant passed.
    violation_count: int = 0
    #: A bounded sample of violations, for a person to read. The full set is
    #: queryable from the run directory. This is what gets shown.
    examples: tuple[str, ...] = ()
    #: True when the invariant could not be evaluated in this context rather than
    #: failing. The INV-10 check (adding an extractor never lowers a claim's tier) compares two
    #: runs, so it needs two.
    not_applicable: bool = False


# ---------------------------------------------------------------------------
# Declarations: the small vocabularies a project extends.
# ---------------------------------------------------------------------------


class ThemeKind(Record):
    """An axis along which the data pool can be explored.

    A theme kind is an attribute *declared as explorable*. File size is an
    attribute you would filter or sort by, but nobody explores the four-megabyte
    theme, so attributes stay ordinary properties and only declared ones become
    axes. That declaration is one record, not a separate mechanism.

    The same declaration drives both uses: it is the axis for exploration, and it
    supplies the respect R when a theme is used for grouping.
    """

    name: str
    definition: str
    origin: str = "builtin"
    enabled: bool = True


class Role(Record):
    """A relationship an entity can play inside one interpretation.

    A role is not a theme kind, and the difference is why this record exists. A theme kind is an
    axis the pool can be explored along, and it is a property of a *sense*: ``antibody`` is a
    reagent wherever it appears. A role is a property of a *reading*: the same antibody is the
    treatment in one experiment, the reference standard in another, and a comparison somebody
    merely mentioned in a third. Nothing about the sense decides which.

    Adding theme kinds can therefore never substitute for recording roles, and the temptation to
    try is strong because both look like labels on a term. The two are declared apart so that
    nobody has to disentangle them from data already written under one heading.

    The record is declared and unpopulated. No role vocabulary ships, nothing assigns a role, and
    no rule may. Introducing the distinction while there is nothing to migrate costs one record and
    one nullable field. Introducing it afterwards would cost a migration and an argument.
    """

    name: str
    definition: str
    origin: str = "builtin"
    enabled: bool = True


class Theme(Record):
    """A materialised subject-matter group: everything sharing one value on one axis.

    Themes overlap freely and do not partition. Membership is the ``concerns``
    primitive. Materialising a theme promotes the value to a node whose
    neighbors are the entities it covers, which is what makes "view the graph by
    this reagent" work.

    Not everything related by ``concerns`` is a theme. Themes are a declared,
    curated set.
    """

    theme_id: str
    theme_kind: str
    value: str
    #: A human-facing label where the raw value is not presentable.
    label: str | None = None
    #: How many entities are members, as of the run that materialised it.
    member_count: int = 0
    run_id: str


class EquivalenceLevel(Record):
    """A named equivalence relation that rules and projections may evaluate at.

    Levels are how the engine has graphs within graphs. Collapsing each class of
    an equivalence relation into one node yields a faithful summary, and the zoom
    levels of the interface are a stack of such quotients under successively
    coarser relations.

    Levels are declared, not built as separate node types populated by separate
    passes. The engine keeps one node population plus a set of named relations,
    materialised on demand. That removes a whole class of bug, where an entity is
    attributed to one group at one level and a different one at another.
    """

    name: str
    #: The respect that defines the equivalence. ``file`` is the identity
    #: relation, where every entity is its own class, and is the default.
    respect: str
    definition: str
    #: Coarser levels have higher numbers. Used to order a zoom stack and to
    #: check that a rule's level is coarser than the data it consumes.
    granularity: int = 0
    #: Temporal windows are per-level parameters rather than one global setting.
    #: Movies relate within a day, experiments within a week and themes within
    #: months. A single global window is wrong at every level but one.
    default_temporal_window_seconds: int | None = None
    origin: str = "builtin"
    enabled: bool = True


class Root(Record):
    """One indexed root of a data pool.

    The logical identifier is separate from the mount path, so a project survives relocation.
    Moving a data pool changes ``mount_path`` and leaves every entry identifier untouched.
    """

    root_id: str
    #: The stable logical name of this root, independent of where it is mounted.
    root_uri: str
    #: Where it was found at the time of the run. Informational, never an input
    #: to identity.
    mount_path: str
    case_sensitivity: CaseSensitivity = CaseSensitivity.UNKNOWN
    #: How ``case_sensitivity`` was established from the root's own names, read-only. It is None
    #: where the label was inherited from the project directory, as it was for every root
    #: declared before this field existed.
    case_established_by: str | None = None
    label: str | None = None
    first_seen_run: str
    last_seen_run: str


# ---------------------------------------------------------------------------
# The Index: entries and observations.
# ---------------------------------------------------------------------------


class Entry(Record):
    """One thing in the data pool, filesystem object or promoted derived stream.

    Every platform-dependent field is nullable. Inode, device, mode, ownership
    and allocated size do not exist everywhere and are not comparable across filesystems. A schema
    that requires them is a schema that lies on some machine.

    Access time is deliberately absent from this record's fingerprint inputs. The
    engine reads files, and a fingerprint that includes access time would record
    the engine's own reading as a change to the file.
    """

    entry_id: str
    entry_kind: EntryKind = EntryKind.FILESYSTEM
    root_id: str

    #: The path as read, preserved verbatim. The identity function normalizes a
    #: copy of this. Nothing here is normalized in place, so the name the user
    #: sees is the name on the disk.
    relative_path: str
    name: str
    parent_entry_id: str | None = None
    depth: int = 0
    entry_type: EntryType = EntryType.FILE
    #: Lowercased final suffix, without the dot. Empty where there is none.
    extension: str = ""
    #: The full multi-part suffix where one exists, such as ``tar.gz``.
    compound_extension: str = ""

    size_bytes: int | None = None
    allocated_bytes: int | None = None
    #: Stored unsigned, as the operating system hands it over: a real inode may lie at or
    #: above 2**63, and macOS's ExFAT driver gives one to every empty file.
    inode: int | None = None
    device: int | None = None
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    nlink: int | None = None

    #: Nanoseconds since the epoch, UTC. Nanoseconds rather than a float because
    #: a float second loses resolution that some filesystems actually provide.
    created_ns: int | None = None
    created_time_source: TimeSource = TimeSource.UNAVAILABLE
    modified_ns: int | None = None
    changed_ns: int | None = None
    #: When the thing this file records actually happened, as against when the
    #: filesystem last touched it. Read from content, or parsed from the name.
    event_time_ns: int | None = None
    #: Where the event time came from. ``event_time_tier`` below says what that source is worth.
    #: The engine records both, because a time with no recorded provenance cannot be reasoned
    #: about.
    event_time_source: TimeSource = TimeSource.UNAVAILABLE
    #: What the source is worth, graded by copy-robustness and not by accuracy (see
    #: :class:`ClockTier`). The tier derives from the source through ``CLOCK_TIER_OF`` and is
    #: stored anyway, so a query can filter on it without a join and a reader can check that the
    #: two agree.
    event_time_tier: ClockTier = ClockTier.UNAVAILABLE

    #: Recorded, never followed.
    symlink_target: str | None = None
    #: Whether that target is there, established by one ``stat`` and never by walking into it.
    #: ``not_a_link`` for everything that is not a symlink.
    symlink_state: SymlinkState = SymlinkState.NOT_A_LINK

    #: Promoted derived streams only. The chain that addresses this stream inside
    #: its parent, empty for every ordinary filesystem entry.
    locator_chain: tuple[LocatorStep, ...] = ()
    #: The transformation that produced a derived entry, and its version.
    transformation: str | None = None
    transformation_version: str | None = None
    #: Disambiguates several products of one transformation.
    ordinal: int | None = None
    #: Content hash of the derived stream. Derived entries have no path, so this
    #: is what makes them recognisable across runs.
    derived_content_hash: str | None = None

    #: Whether the questions asked about this entry have been settled, and if
    #: not, why not. Never a null.
    resolution_status: ResolutionStatus = ResolutionStatus.UNEXAMINED
    #: True when a later scan found the entry gone. Deletion is recorded, never
    #: applied: the row stays and gains a tombstone.
    is_deleted: bool = False

    first_seen_run: str
    last_seen_run: str

    # -- reuse -----------------------------------------------------------
    # The paired fingerprints that decide whether a re-scan may skip this entry. They are stored
    # and not recomputed. The algorithm version is both a prefix on the value and a field inside
    # the hashed payload. So a record made by an older generation cannot silently compare equal
    # to one made by a newer one. Recomputing both sides with today's algorithm would destroy that
    # property.

    #: Did the file change? A narrow hash over size, modification time, change
    #: time and file kind. Everything else is deliberately excluded.
    stat_fingerprint: str | None = None
    #: Would the engine do the same work today? A hash over the policy that
    #: produced this record, including the versions of the extractors that
    #: claim this file's extension.
    processing_fingerprint: str | None = None
    #: What the last run did with this entry. Decides whether the record
    #: delivers what today's policy asks for.
    scan_outcome: ScanOutcome = ScanOutcome.METADATA_ONLY
    #: The depth profile this entry's content was last read at (schema 1.22): ``standard``,
    #: ``deep`` and the rest. ``None`` where content was never read. A deeper pass can choose a
    #: file by the evidence it still lacks and not only by how the last reading ended. A file
    #: read in full under a shallow profile is not a file read deeply.
    extraction_profile: str | None = None

    # -- moves (1.22) ----------------------------------------------------
    #: The entry an earlier census held at another path, where this one is that file moved. A walk
    #: with a parent census writes it, reading both censuses and nothing else, so the Index can
    #: reproduce it. ``None`` for every entry not known to have moved. A decision about the
    #: earlier entry is read at this one, and nothing is written on anybody's behalf.
    moved_from: str | None = None
    #: How the move was known. Set exactly when ``moved_from`` is.
    moved_by: MovedBy | None = None
    #: When the move happened, at the latest: the entry's change time where it fell between the
    #: walk that last saw the file at its old place and the walk that found it here. A move
    #: changes a file's change time and nothing later than the move can have made it earlier, so
    #: the move happened at or before it. ``None`` where the change time was outside that window
    #: or absent. This is never the file's clock: it says when the file was moved, not when
    #: anything it records happened.
    moved_at_ns: int | None = None

    @model_validator(mode="after")
    def _a_move_says_how_it_was_known(self) -> Entry:
        """``moved_from`` and ``moved_by`` are one fact and are set together or not at all."""
        if (self.moved_from is None) != (self.moved_by is None):
            message = "an entry's moved_from and moved_by are set together or not at all"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _derived_entries_declare_their_origin(self) -> Entry:
        """A derived entry must say what produced it and where it sits."""
        if self.entry_kind is EntryKind.DERIVED:
            if not self.locator_chain:
                message = "a derived entry requires a non-empty locator chain"
                raise ValueError(message)
            if self.transformation is None:
                message = "a derived entry requires a transformation"
                raise ValueError(message)
            if self.parent_entry_id is None:
                message = "a derived entry requires a parent entry"
                raise ValueError(message)
        return self


class Observation(Record):
    """Something read from storage. Immutable, and never revised.

    An observation's only claim is that a named observer, at a named version,
    read this from this location at this time. A later scan produces a *new*
    observation and both are kept, because the difference between two readings is
    itself evidence.

    Carries ``extraction_tier``, which measures distance from the bytes. It never
    carries an inference tier: an observation is not a conclusion, and the two
    axes are kept apart by keeping the models apart.
    """

    observation_id: str
    entry_id: str
    run_id: str
    #: The extractor, identity method or scanner that produced this.
    observer: str
    observer_version: str
    observed_at: str

    #: What was observed, as a stable key: ``size_bytes``, ``exif.DateTimeOriginal``,
    #: ``schema_hash``. Keys are the observer's vocabulary and are not enumerated
    #: here, because enumerating them would make adding an extractor a schema
    #: change.
    key: str
    value_kind: ValueKind = ValueKind.TEXT
    value_text: str | None = None
    value_integer: int | None = None
    value_real: float | None = None
    value_boolean: bool | None = None
    #: Canonically serialized structure, for ``STRUCTURE`` values.
    value_structure: str | None = None

    extraction_tier: ExtractionTier = ExtractionTier.LITERAL
    source_scope: SourceScope = SourceScope.CONTENT
    #: Where inside the entry this came from. Most observations about the
    #: interior of a file belong on that file's entry with a locator, rather than
    #: becoming an entry of their own.
    locator_chain: tuple[LocatorStep, ...] = ()

    #: For identity observations: the method, its version and how much was read.
    identity_method: str | None = None
    identity_method_version: str | None = None
    identity_scope: IdentityScope | None = None

    @model_validator(mode="after")
    def _absence_tiers_carry_no_value(self) -> Observation:
        """An observation that records no reading must not also carry a reading.

        ``inaccessible`` and ``not_examined`` say the ladder was never climbed.
        A value alongside one of them would be a reading labeled as no reading.
        """
        if self.extraction_tier in (ExtractionTier.INACCESSIBLE, ExtractionTier.NOT_EXAMINED):
            values = (
                self.value_text,
                self.value_integer,
                self.value_real,
                self.value_boolean,
                self.value_structure,
            )
            if any(value is not None for value in values):
                message = (
                    f"extraction tier {self.extraction_tier.value!r} records that nothing "
                    f"was read, so the observation must carry no value"
                )
                raise ValueError(message)
        return self


class Component(Record):
    """One independently comparable normalized value drawn from an entry.

    Normalization is the mechanical half of synonym resolution. It needs no user input, which is
    why it runs first and exhaustively. ``500nM``, ``500 nm`` and ``500NM`` become one component
    with a magnitude and a dimension.

    The normalizer and its version are part of the component's identity, so improving a
    normalizer produces new components and does not rewrite old ones. A past run stays
    replayable.
    """

    component_id: str
    entry_id: str
    run_id: str
    component_type: str
    normalized_value: str
    #: The string as it appeared, kept so a normalization can be disputed.
    raw_value: str | None = None
    source_scope: SourceScope = SourceScope.NAME
    normalizer: str
    normalizer_version: str
    extraction_tier: ExtractionTier = ExtractionTier.HEURISTIC

    #: For quantities: the numeric magnitude and the dimension it was reduced to.
    magnitude: float | None = None
    dimension: str | None = None
    unit: str | None = None

    #: How many times this component occurred in what it was drawn from.
    #:
    #: There is one component per distinct value, counted and not repeated. A term appearing
    #: three times in one name is a different fact from a term appearing once. Discarding the
    #: repeats loses that fact. Storing three rows under one identifier also loses it, because
    #: the count then depends on how many rows a reader happens to see. The count lets a later
    #: rule tell apart compounding across distinct senses and repeats of one spelling.
    #:
    #: The entry's name shape holds one slot per occurrence, so the count is recoverable from
    #: it. It is stored anyway, because the shape is gated on a setting and counting slots inside
    #: a serialized column is far more expensive than reading an integer. A test asserts the two
    #: agree.
    occurrences: Annotated[int, Field(ge=1)] = 1
    #: What the Dictionary made of this component is not stored here. It lives in
    #: :class:`DictionaryBinding`, one primary-key join away. A token read off the
    #: filesystem and a sense resolved from it sit at different distances from the bytes, and a
    #: single tier column cannot describe both.
    #: The observations this component was normalized from.
    observations: tuple[str, ...] = ()


class NameSlot(Record):
    """One position in an entry's name, and what stands there.

    A slot addresses an occurrence, and a component addresses a value. The distinction matters.
    A component is deduplicated to one row per distinct normalized value per entry, so it keeps
    the *first* spelling of several normalized-equivalent occurrences and nothing else. Two
    instances of ``40min`` in one name would share a component row. With a start offset but no
    end, they could not be told apart or read back off the name. Every layer above this one
    (grammar, role assignment, attachment) needs the occurrence and not the value, so the slot
    records it.
    """

    #: Position in the name, counting from zero. The slot's identity within its
    #: shape, and what a rule compares across two names.
    ordinal: int
    #: The component standing here. Two slots of one shape may carry the same
    #: component identifier, which is precisely what a repeated term looks like
    #: and is why deduplicating components loses nothing.
    component_id: str
    #: The atom kind, copied so the common filter needs no join.
    component_type: str
    #: The theme kind this resolved to, where the Dictionary recognized it.
    theme_kind: str | None = None
    #: The separator text immediately before this slot, empty for the first.
    #: Kept because a shape without its separators cannot be compared to a name.
    separator: str = ""
    #: Character offset in the name as read.
    start: int = 0
    #: Character offset one past the last character, so ``name[start:end]`` is
    #: this occurrence's exact text. Without the end offset, a repeated term's second spelling
    #: is unrecoverable: the component kept the first, and a start with no length cannot slice
    #: the name to find the rest.
    end: int = 0
    #: Which separator-delimited fragment of the name this slot came from, from
    #: zero. Slots sharing a group were written together and slots in different
    #: groups were not, which is what lets a group of occurrences be addressed
    #: as one thing.
    #:
    #: The structure is minimal on purpose. Two instances of ``40min`` in one name may be one
    #: clause repeated or two different clauses, and nothing else stored says which. A fragment
    #: index says it, costs one integer, and does not commit the engine to a grammar it has not
    #: induced. A richer nesting would be a claim about structure and not a record of layout.
    group: int = 0
    #: The relationship this occurrence plays inside one interpretation.
    #:
    #: Declared and never populated. A role is not a theme kind. A theme kind is the axis a
    #: sense sits on, and it is a property of the sense. A role is a relationship inside one
    #: reading, and it is a property of that reading. The same reagent is a treatment in one
    #: experiment and a control in another, so no amount of theme-kind vocabulary can stand in
    #: for it. The field is declared early so that adding it later needs no migration. Nothing
    #: writes it and no rule assigns one, and a test asserts that this stays true.
    role: str | None = None
    #: The whole spelling a capital divided (schema 1.22), on every piece of such a run and on
    #: nothing else: ``HaloTag`` read as ``Halo`` and ``Tag`` carries ``HaloTag`` on both slots.
    #: The tokenizer reads one name alone and records the division. Whether the laboratory uses
    #: the glued spelling as one word is the binder's choice. The binder reads it from the run's
    #: whole vocabulary and the Record, so a component's identity never depends on how many
    #: other names exist.
    case_whole: str | None = None

    @model_validator(mode="after")
    def _an_occurrence_has_a_positive_extent(self) -> NameSlot:
        """A slot that ends before it starts addresses nothing."""
        if self.end < self.start:
            message = f"slot {self.ordinal} ends at {self.end}, before its start at {self.start}"
            raise ValueError(message)
        return self


class NameShape(Record):
    """Where the parts of one entry's name sat, held apart from what they were.

    Content and position do different work, so the record stores them apart. What a file is about
    is conceptual and lives in :class:`Component`. Which files form a series with it is
    relational and lives here. Putting an ordinal on a component would entangle the two and would
    force a second decision about whether that ordinal belongs in the component's identity.
    Keeping them apart enforces the separation in storage and not by discipline. It also makes
    the question of whether content and position should be weighed together or separately a
    rule-level experiment and not a storage rewrite.

    The record exists to enable one kind of rule. Files in one folder that share a skeleton and
    differ at exactly one slot are ``equivalent`` in the respect the skeleton supplies, and the
    differing slot names the relation. A bare number is a replicate series and ``succeeds``. A
    date is a time series. A quantity whose dimension is a concentration is a dose series. A rule
    that reads a shape and its one differing slot can emit those edges with a stated respect, and
    it needs no learning. The replicate grouping (``rulebook/predicates/group.py``) and the series
    ordering (``rulebook/predicates/series.py``) read this record.
    """

    name_shape_id: str
    entry_id: str
    run_id: str
    source_scope: SourceScope = SourceScope.NAME
    normalizer: str
    normalizer_version: str

    #: The name with its content abstracted away: a recognized term becomes its
    #: theme kind, an unrecognized run becomes a placeholder, separators and
    #: order are kept. Two names built to one template produce one skeleton,
    #: which is what makes "files in this folder sharing a shape" a group-by
    #: rather than a comparison of every pair.
    skeleton: str
    #: Digest of the skeleton, so that grouping is an equality on a short column
    #: rather than on a string of unbounded length.
    skeleton_hash: str
    slot_count: Annotated[int, Field(ge=0)] = 0
    slots: tuple[NameSlot, ...] = ()


# ---------------------------------------------------------------------------
# Inference: claims, candidates, assertions.
# ---------------------------------------------------------------------------


class Claim(Record):
    """A statement the engine derived. Never an observation, and never stored as one.

    Every claim names the rule that licensed it and the version of that rule. It also lists
    the Index records it consumed, so any conclusion can be walked back to
    the bytes mechanically rather than by reading a prose evidence string.

    A defeasible or abductive claim must be able to say what would retract it.
    That is what makes retraction an event rather than an erosion.
    """

    claim_id: str
    run_id: str
    #: What the claim is about: an entry, an edge, a theme, an activity.
    subject: str
    predicate: str
    object_reference: str | None = None

    inference_tier: InferenceTier = InferenceTier.DEFEASIBLE
    rule_id: str
    rule_version: str
    scope: Scope

    #: The Index records consumed: observations, and for a classification the
    #: bindings, components or entry that carry the value. Provenance of the
    #: inference, not of the file.
    inputs: tuple[str, ...] = ()
    #: The evidence, kept whole. Never summed before the decision is made.
    support: tuple[SupportComponent, ...] = ()
    #: What would retract this claim, and whether it has fired.
    defeaters: tuple[Defeater, ...] = ()
    salience: Salience | None = None
    resolution_status: ResolutionStatus = ResolutionStatus.RESOLVED

    @model_validator(mode="after")
    def _retractable_claims_name_their_defeaters(self) -> Claim:
        """A claim that can be wrong must say what would show it wrong."""
        if self.inference_tier in (InferenceTier.DEFEASIBLE, InferenceTier.ABDUCTIVE) and (
            not self.defeaters
        ):
            message = (
                f"a {self.inference_tier.value} claim must declare at least one defeater; "
                f"a conclusion that names nothing that would retract it is not defeasible"
            )
            raise ValueError(message)
        return self


class Candidate(Record):
    """A proposed edge, before the decoder has chosen a globally consistent subset.

    Candidates are generated within bounded indexes, never all-pairs. Each carries
    its evidence vector whole and a decision tier assigned by classification
    rather than by threshold. A score exists only to order candidates within one
    tier.
    """

    candidate_id: str
    run_id: str
    source_id: str
    target_id: str
    primitive: Primitive
    respect: str | None = None
    specialization: str | None = None

    rule_id: str
    rule_version: str
    scope: Scope
    #: The equivalence level the proposing rule evaluated at.
    level: str = "file"

    decision_tier: DecisionTier = DecisionTier.CIRCUMSTANTIAL
    support: tuple[SupportComponent, ...] = ()
    defeaters: tuple[Defeater, ...] = ()
    salience: Salience | None = None
    #: Orders candidates within one decision tier. Not a probability, not
    #: comparable across tiers, and never an acceptance threshold.
    tiebreak: float = 0.0

    @model_validator(mode="after")
    def _respect_is_present_where_the_primitive_requires_it(self) -> Candidate:
        """Equivalence without a stated dimension is not a claim."""
        if PRIMITIVE_PROPERTIES[self.primitive].requires_respect and self.respect is None:
            message = f"primitive {self.primitive.value!r} requires a respect"
            raise ValueError(message)
        return self


class Assertion(Record):
    """A published edge.

    Identity derives from source, target, primitive, respect, rule, rule version
    and scope, and from nothing else. The evidence that justified the edge lives
    in the fields below, beside the identifier rather than inside it, so evidence
    can be revised without the edge becoming a different edge.
    """

    edge_id: str
    run_id: str
    source_id: str
    target_id: str
    primitive: Primitive
    respect: str | None = None
    #: The named specialization, where one applies. Resolved through the
    #: specialization registry. Nothing addresses a specialization by name.
    specialization: str | None = None

    rule_id: str
    rule_version: str
    scope: Scope
    #: The equivalence level this edge lives at. ``file`` is the finest.
    level: str = "file"
    #: Whether a coarse edge was lifted from edges between members or asserted
    #: directly at this level. Some relationships exist only at a coarse level
    #: and have no fine-grained counterpart.
    quotient_origin: QuotientOrigin = QuotientOrigin.NATIVE

    inference_tier: InferenceTier = InferenceTier.DEFEASIBLE
    decision_status: DecisionStatus = DecisionStatus.ASSERTED
    #: The candidate this was promoted from, where it came from one.
    candidate_id: str | None = None
    support: tuple[SupportComponent, ...] = ()
    defeaters: tuple[Defeater, ...] = ()

    #: Required on every ``embeds`` edge. Why expansion stopped, so the absence
    #: of a sibling edge is readable.
    expansion_state: ExpansionState | None = None
    #: Recorded on every ``references`` edge. How well the reference string
    #: resolved, since exact, ambiguous and unresolved are different findings.
    reference_resolution: ReferenceResolution | None = None
    #: Where both endpoints have extents, the qualified temporal relation.
    allen_relation: AllenRelation | None = None
    #: Which clock the ordering rests on, where the edge used one.
    clock: ClockKind | None = None
    #: The activity node, where the process that produced the edge is known.
    activity_id: str | None = None
    #: The concerns weight, which must be earned against the null model.
    weight: float | None = None

    @model_validator(mode="after")
    def _primitive_obligations_are_met(self) -> Assertion:
        """Enforce, at construction, what each primitive declares it requires.

        Checking here as well as in the invariant suite is deliberate. The
        invariants block publication of a whole graph. This stops a malformed
        edge being built in the first place, which is where the error is
        cheapest to understand.
        """
        properties = PRIMITIVE_PROPERTIES[self.primitive]
        if properties.requires_respect and self.respect is None:
            message = f"primitive {self.primitive.value!r} requires a respect"
            raise ValueError(message)
        if properties.requires_expansion_state and self.expansion_state is None:
            message = (
                f"primitive {self.primitive.value!r} requires an expansion state, "
                f"without which the absence of a sibling edge is unreadable"
            )
            raise ValueError(message)
        if self.primitive is Primitive.REFERENCES and self.reference_resolution is None:
            message = "a references edge must record how its reference string resolved"
            raise ValueError(message)
        return self


# ---------------------------------------------------------------------------
# The Rulebook.
# ---------------------------------------------------------------------------


class RuleEffect(StrEnum):
    """What a rule does. This is the type system of the Rulebook.

    A rule's effect says where in the pipeline it runs, so the engine derives rule ordering and
    does not configure it. That prevents the Rulebook from becoming an unordered pile whose
    behavior depends on file order.
    """

    #: Constrains the decoder. Expresses impossibility, which a score cannot.
    CONSTRAINT = "constraint"
    #: Proposes candidate edges.
    PROPOSES = "proposes"
    #: Assigns a classification to an entity.
    CLASSIFIES = "classifies"
    #: Defines an equivalence relation, and therefore a grouping and a level.
    GROUPS = "groups"
    #: Raises something for a person's attention without concluding anything.
    FLAGS = "flags"
    #: Establishes what the pool holds while it is enumerated or a container is opened.
    #: Run by the scan layer, and never by the inference pass.
    ENUMERATES = "enumerates"


class RuleStatus(StrEnum):
    """Whether a person has confirmed a rule, on :class:`DictionaryStatus`'s precedent.

    The status is kept out of the digest, as the rationale is. Confirming a rule changes nothing
    it concludes, so it must not make two runs of one Rulebook look different.
    """

    ACTIVE = "active"
    #: In force and awaiting a person's confirmation in the Record, or validation. The scan
    #: layer's own rules ship this way, and while one does no project revision may switch
    #: it off, supersede it or tune it.
    PROVISIONAL = "provisional"


class ParameterType(StrEnum):
    """The type of a rule parameter.

    Parameters are typed and bounded so the interface renders a control without bespoke code: an
    enumeration becomes a dropdown, a bounded number a slider, a list a chip editor. This is how
    a parameter becomes a real control and not a line in a configuration file.
    """

    BOOLEAN = "boolean"
    INTEGER = "integer"
    REAL = "real"
    STRING = "string"
    ENUM = "enum"
    #: A list of strings: a phrase list, an extension list, a denylist.
    STRING_LIST = "string_list"
    #: A mapping from string to string: an extension-to-role table.
    MAPPING = "mapping"
    #: Seconds. Separate from ``INTEGER`` so an interface can render a duration.
    DURATION = "duration"


class ParameterSpec(Record):
    """One typed, bounded, documented rule parameter."""

    name: str
    type: ParameterType
    #: Canonically serialized, because a default can be a list or a mapping.
    default: str
    #: Permitted values for an enumeration.
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: What this parameter does, in one sentence, for the interface to show.
    description: str = ""


class RuleComposition(Record):
    """Boolean composition over predicates.

    Boolean composition is enough here, and a general expression language is not needed. It
    expresses "treat two folders as replicates when the conditions match and the dates differ and
    neither is named like a rerun", which covers the realistic cases. The grammar leaves room for
    an expression syntax without anything depending on one.
    """

    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()


class Rule(Record):
    """A named, versioned, toggleable, parameterised statement licensing one inference.

    The English sentence and the executable form are two renderings of one object, not two
    objects kept in sync. The sentence is a template with typed slots. A person edits slots, and
    the interface labels, the rendered Rulebook document and the reference documentation all come
    from this one declaration.
    """

    rule_id: str
    version: str
    #: The primitive this rule concerns, where it concerns one. A ``flags`` rule
    #: often concerns none.
    primitive: Primitive | None = None
    #: The specialization this rule asserts, where the catalog names one.
    #: ``replicate_of`` and ``same_condition_as`` are both ``equivalent`` in
    #: respect ``experimental_condition`` and mean different things, so the
    #: respect alone cannot tell them apart and the name has to be sayable.
    #: Resolved through the specialization registry. Never imported by name.
    specialization: str | None = None
    #: The respect an equivalence is asserted in. Supplied by the specialization
    #: where one is named, and stated directly where none covers the case.
    #: Derived rather than stored twice, so the two can never disagree.
    respect: str | None = None
    #: The template, with ``{parameter}`` slots filled from ``parameters``.
    statement: str
    parameters: tuple[ParameterSpec, ...] = ()
    scope: Scope
    #: The equivalence level the rule evaluates at. A rule at level
    #: ``experiment`` compares experiments rather than files, and its temporal
    #: window is measured in weeks rather than minutes.
    level: str = "file"
    #: Dotted path of the registered predicate. Bound through the rulebook
    #: registry. Never imported directly.
    predicate: str
    #: The registered predicate's version at the moment this rule was loaded.
    #:
    #: The version is read off the registration and not declared in the rule file, for the same
    #: reason a specialization's respect is. It is a fact about the implementation, and a rule
    #: file stating it would be a second copy that could disagree. A predicate bumps this
    #: whenever its verdict could change for unchanged input. That is the event a run needs in
    #: order to re-decide what it affected, so the version belongs in the digest beside the
    #: parameters.
    predicate_version: str = ""
    effect: RuleEffect
    #: The kind of reasoning this rule produces. A rule cannot emit a claim at a
    #: stronger tier than it declares, and the one place that builds a claim gives it exactly
    #: this tier.
    tier: InferenceTier = InferenceTier.DEFEASIBLE
    #: The rules whose conclusion about a subject would retract this rule's claim
    #: about it, by identifier.
    #:
    #: A defeat condition is itself a rule, with a sentence a person approved, a predicate, a
    #: version and parameters. A retraction then traces to something a person can read, exactly
    #: as a conclusion does. A defeasible or abductive rule naming none licenses no claim. It
    #: still loads, because a rule written before this field existed names none.
    defeated_by: tuple[str, ...] = ()
    #: The index a proposing rule draws its candidate pairs within: a grouping rule's
    #: identifier, or ``parent`` for the files of one folder. Candidates are never drawn from the
    #: whole pool, so a proposing rule naming none proposes nothing. The field is refused on any
    #: other effect when a file is read, and when the Rulebook composes where it names no
    #: grouping rule a revision declares.
    candidates_within: str | None = None
    enabled: bool = True
    #: Whether a person has confirmed the rule. Shown with the rule, stored with each
    #: run's rules, and kept out of the digest. A provisional rule stays in force as shipped.
    status: RuleStatus = RuleStatus.ACTIVE
    #: Required, and not decorative. A rule that cannot be justified in one
    #: sentence is a heuristic in disguise and should be marked as one.
    rationale: str = Field(min_length=1)
    composition: RuleComposition | None = None
    origin: str = "builtin"
    #: The revision this rule entered at. Superseding marks a rule inactive from
    #: a revision forward and never removes history, so any past run replays.
    added_revision: int = 0
    superseded_revision: int | None = None

    @model_validator(mode="after")
    def _respect_is_present_where_the_primitive_requires_it(self) -> Rule:
        """A rule asserting an equivalence must say in what respect.

        This is the same check that :class:`Candidate` makes, one layer earlier. The engine
        makes this check twice: at construction and again at publication. Making the check here
        means a rule that could only ever produce an invalid candidate is refused when the file
        is read. It is not refused when the first candidate is built. The person who can fix it
        is reading the file at that moment.

        The check works in both directions, and the second is narrower than it first looks. A
        respect that a rule invents for a primitive that has none is a claim the relation cannot
        carry. It would render into the sentence a person approves while corresponding to nothing
        that runs. A respect that a specialization fixes is a different thing. ``concerns``
        requires none, and ``about_technique`` fixes ``technique`` under it. That is the catalog
        saying what the narrower relation is about. So the refusal is of a freely declared
        respect, not of every respect on a primitive that does not demand one. Naming a
        specialization tells the two apart without consulting the catalog from inside a record.
        """
        if self.primitive is None:
            if self.respect is not None:
                message = (
                    f"rule {self.rule_id!r} states a respect and names no primitive; "
                    f"a respect is a dimension of a relation and there is no relation here"
                )
                raise ValueError(message)
            return self
        required = PRIMITIVE_PROPERTIES[self.primitive].requires_respect
        if required and self.respect is None:
            message = (
                f"rule {self.rule_id!r} asserts {self.primitive.value!r}, which requires "
                f"a respect; an equivalence without a stated dimension is not a claim"
            )
            raise ValueError(message)
        if not required and self.respect is not None and self.specialization is None:
            message = (
                f"rule {self.rule_id!r} asserts {self.primitive.value!r}, which takes no "
                f"respect, and states {self.respect!r} without a specialization fixing it"
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _defeaters_are_named_only_where_a_claim_can_be_retracted(self) -> Rule:
        """A rule may not defeat itself, and a claim that cannot be retracted names no defeater.

        This reads the same tiers that the :class:`Claim` validator requires defeaters on, in the
        other direction. An entailed claim cannot be wrong if its observations are right, and
        testimony outranks every rule within its scope. A defeater named on either would be a
        retraction condition that nothing can ever meet.
        """
        if self.rule_id in self.defeated_by:
            message = f"rule {self.rule_id!r} names itself as what would defeat it"
            raise ValueError(message)
        if len(set(self.defeated_by)) != len(self.defeated_by):
            message = f"rule {self.rule_id!r} names one defeater twice"
            raise ValueError(message)
        retractable = self.tier in (InferenceTier.DEFEASIBLE, InferenceTier.ABDUCTIVE)
        if self.defeated_by and not retractable:
            message = (
                f"rule {self.rule_id!r} is {self.tier.value}, which cannot be retracted, "
                f"and names defeaters"
            )
            raise ValueError(message)
        return self


# ---------------------------------------------------------------------------
# The Dictionary.
# ---------------------------------------------------------------------------


class DictionaryOrigin(StrEnum):
    """Where a Dictionary entry came from."""

    BUILTIN = "builtin"
    PACK = "pack"
    USER = "user"
    #: Induced from this corpus. A proposal until a person promotes it.
    DISCOVERED = "discovered"


class MatchQuality(StrEnum):
    """How a token matched a Dictionary surface, which is evidence in itself.

    Case matters in scientific notation, and losing it is not a small error. ``nM`` is nanomolar
    and ``nm`` is nanometre. ``mM`` is millimolar and ``mm`` is a millimetre. A lookup that folds
    case reports a length where a concentration was written, wrong by nine orders of magnitude
    and silently so.

    The enum lives here and not beside the lookup that computes it, because a record field cannot
    reference a type owned by a subpackage that imports this one.
    """

    #: Matched exactly as written. The only quality that preserves case-sensitive
    #: unit symbols, and therefore the only one a unit may be read from safely.
    EXACT = "exact"
    #: Matched ignoring case. Right for words, unsafe for symbols.
    CASE_FOLDED = "case_folded"
    #: Matched after punctuation was stripped too. A guess, and marked as one.
    LOOSE = "loose"
    #: A spelling bound to a near Dictionary spelling (a typing slip the engine read past).
    #: Weaker than any other quality, so a rule whose floor is ``loose`` still refuses it: the name
    #: did not write the spelling the sense was reached by.
    CORRECTED = "corrected"


#: The qualities from strongest to weakest, which is the order anything ranking
#: or thresholding on one needs.
#:
#: The order is declared here beside the enum and not beside either consumer. The resolver needs
#: it to pick the best of several spellings, and a rule needs it to refuse a match that was
#: reached by guessing. Two copies of an order eventually disagree about which end is which.
MATCH_QUALITY_ORDER: tuple[MatchQuality, ...] = (
    MatchQuality.EXACT,
    MatchQuality.CASE_FOLDED,
    MatchQuality.LOOSE,
    # Last: weaker than a guess, so no floor named before this quality existed lets it through.
    MatchQuality.CORRECTED,
)


def match_quality_meets(quality: MatchQuality | None, floor: MatchQuality) -> bool:
    """Whether a match is at least as strong as a stated floor.

    ``None`` fails every floor. A binding with no recorded quality is one that resolved through a
    path that does not produce one. Treating an absent grade as a passing grade is how an
    absence becomes a claim.
    """
    if quality is None:
        return False
    return MATCH_QUALITY_ORDER.index(quality) <= MATCH_QUALITY_ORDER.index(floor)


class BindingMethod(StrEnum):
    """How a resolver reached a sense.

    The path is recorded and not derived, because the two paths do not agree about precedence. A
    surface lookup ranks candidates by scope and then origin and returns the winner. The unit
    table resolves a term directly and returns whichever section was loaded last, so the origin
    on such a binding is not the origin of a precedence winner. A rule attenuating by origin has
    to be able to exclude those rows. Inferring which path ran from the component's type would be
    exactly the habit this record exists to break.
    """

    #: Resolved through the scoped, precedence-ordered surface lookup.
    SURFACE = "surface"
    #: Resolved through the normalizer's unit table, by term rather than surface.
    UNIT_TABLE = "unit_table"


class DictionaryStatus(StrEnum):
    """Whether a Dictionary entry is in force."""

    ACTIVE = "active"
    #: Proposed and awaiting confirmation. A token sitting in a known positional
    #: slot but absent from the Dictionary becomes one of these.
    PROPOSED = "proposed"
    #: Rejected by a person. Kept, so it is not proposed again every run.
    REJECTED = "rejected"


class AreaKind(StrEnum):
    """What an area of a project is: the thing a top-level folder stands for.

    An area is the folder the engine groups experiments by. Its kind says what the folder is
    named for, so a page can say "by person", "by instrument" or "by year" instead of "by
    folder". The kind is read from the names and assumed until a person confirms it
    (``POST /api/areas/kind``).
    """

    PERSON = "person"
    INSTRUMENT = "instrument"
    DATE = "date"
    PROJECT = "project"
    FOLDER = "folder"


class AssumptionKind(StrEnum):
    """What kind of thing the engine assumed without asking, as the assumptions page lists it.

    Each is a reading the run rests on that nobody confirmed: what a file type is, which
    program wrote a file, which domain a word belongs to, which spelling a slip meant, what
    an area's folder stands for, which workflow an experiment follows, which stage a file is
    at, and which files are repeats of one another. Rejecting one waits for Re-run.
    """

    FILE_TYPE = "file_type"
    WRITER = "writer"
    DOMAIN = "domain"
    SPELLING = "spelling"
    AREA_KIND = "area_kind"
    WORKFLOW = "workflow"
    STAGE = "stage"
    REPEAT = "repeat"


class DictionaryEntry(Record):
    """What a string denotes when it appears in this data pool.

    A single hit is weak evidence: enough to propose, never enough to assert. Independent hits
    compound, but only across distinct senses after surface normalization. Three spellings of one
    term are one fact seen three times and must not compound, or every verbose filename becomes
    maximally confident.

    An entry only contributes evidence when something else corroborates it. So an exhaustive
    Dictionary costs almost nothing: an irrelevant entry never fires and a user never sees it.
    """

    term_id: str
    term: str
    #: Every observed spelling that maps to this term.
    surfaces: tuple[str, ...] = ()
    #: The axis this term names a value on.
    theme_kind: str
    #: The canonical human-facing form.
    canonical: str
    scope: Scope
    origin: DictionaryOrigin = DictionaryOrigin.BUILTIN
    status: DictionaryStatus = DictionaryStatus.ACTIVE
    #: Why this entry is believed, for a discovered term especially.
    evidence: str = ""
    added_revision: int = 0
    #: Opaque identifiers in external vocabularies: units, data types,
    #: experimental terms. They cost nothing, enable interoperability, and are never required.
    ontology_ids: tuple[str, ...] = ()

    #: A digest of everything that identifies this sense's content: its term,
    #: scope, origin, status, theme kind, canonical form and ordered surfaces.
    #: The per-sense version the Dictionary otherwise lacks. ``dictionary_hash``
    #: on the run record versions the whole composed vocabulary, which cannot answer "did *this*
    #: sense change between two runs". This digest can.
    sense_digest: str = ""

    #: Which section file this came from, and the field it belongs to where it
    #: belongs to one. These are real fields and not prose inside ``evidence``, because "ignore
    #: the vendor-specific sections" has to be a filter and not a sentence somebody parses.
    section: str = ""
    domain: str | None = None


class BindingAlternative(Record):
    """One sense that stood equal to the winner, or was shadowed by it.

    An ambiguity is not resolved by picking. Where two senses stand equal, nothing about the token
    distinguishes them and choosing would invent a fact. Where one shadowed the other, a rule
    attenuating the winner needs somewhere to fall back to, and a lookup that discarded the loser
    leaves it nowhere.
    """

    term_id: str
    theme_kind: str
    origin: DictionaryOrigin
    #: The precedence value in force during the run that wrote this, so a later release that
    #: reorders precedence cannot change a past answer.
    origin_rank: int
    scope: Scope
    #: True where this sense lost on precedence rather than tying with the
    #: winner. A tie means the engine cannot tell. A loss means it chose.
    shadowed: bool = False


class DictionaryBinding(Record):
    """What the Dictionary made of one component, held apart from the component.

    The token and its meaning sit at different distances from the bytes. A filename was read
    literally off the filesystem, and :class:`Component` records that, at ``LITERAL``. Resolving
    ``md`` to a sense is interpretation of meaning and not of form, which is the definition of
    ``SEMANTIC``, the last rung of the ladder. One record carrying one tier for both would say a
    Dictionary interpretation was as certain as a ``stat`` call.

    This record carries nothing read from the filesystem: no raw value, no normalized value, no
    magnitude, no occurrence count. Every literal is one primary-key join away on
    ``component_id``. That keeps observations and interpretations apart by construction and not
    by discipline.

    There is one row per component, always written when the binder ran, including when it
    declined. The absence of a row is then never a fact about a component. It can only mean that
    the binder did not run, and the run record says so. That keeps "not examined" distinct from
    "examined and found nothing", which a bare null cannot express.
    """

    binding_id: str
    #: The component this resolves. The identity spine and the join key.
    component_id: str
    #: Denormalised, because every rule that reads this table groups by entry,
    #: and recovering it otherwise means joining the largest table in the run.
    entry_id: str
    run_id: str
    #: Denormalised for the same reason, and for a sharper one: a content binding
    #: must not compound with a name binding, and without this column that
    #: distinction is a join.
    source_scope: SourceScope = SourceScope.NAME

    resolver: str
    resolver_version: str
    method: BindingMethod = BindingMethod.SURFACE
    #: The scope the lookup ran under, which is not the winning sense's scope.
    lookup_scope: Scope
    #: A short token naming any restriction placed on the candidate set, such as
    #: ``extension:format``. Null when unconstrained. Without it, a token that
    #: abstained because a constraint excluded its only sense is indistinguishable
    #: from one nobody has ever heard of. The discovery surface then offers to
    #: add a term the Dictionary has had all along.
    constraint: str | None = None

    #: Constant by construction except where nothing was examined. This is the
    #: only place in the engine that assigns the semantic rung.
    extraction_tier: ExtractionTier = ExtractionTier.SEMANTIC
    resolution_status: ResolutionStatus = ResolutionStatus.RESOLVED

    term_id: str | None = None
    #: The sense's readable name, denormalised beside its identifier.
    #:
    #: Without it the Rulebook can constrain by axis and never by sense. ``term_id`` hashes term,
    #: scope and origin, so a rule wanting to say that a vendor acquisition format is raw data
    #: would have to carry a hash in a parameter. A user is meant to open the rule that licensed
    #: a conclusion and read it. A parameter holding an opaque identifier fails that outright,
    #: and one holding a name the engine cannot match fails it more quietly.
    #:
    #: ``entry_id`` and ``source_scope`` are denormalised here too, because every rule reading
    #: this table would otherwise join the largest table in the run to recover them. ``term`` is
    #: one of the three inputs to the identifier beside it, so the two cannot disagree without
    #: the identifier being wrong.
    term: str | None = None
    #: Populated independently of ``term_id``. Where every candidate shares one
    #: theme kind, the axis is settled even though the value is not, and this
    #: says so.
    theme_kind: str | None = None
    quality: MatchQuality | None = None

    sense_origin: DictionaryOrigin | None = None
    sense_origin_rank: int | None = None
    sense_scope: Scope | None = None
    sense_scope_rank: int | None = None
    sense_status: DictionaryStatus | None = None
    sense_digest: str | None = None

    #: Redundant with the length of the list below, for the same reason as ``occurrences``:
    #: counting inside a serialized column is far more expensive than reading an integer. A test
    #: asserts the two agree.
    alternative_count: int = 0
    alternatives: tuple[BindingAlternative, ...] = ()

    @model_validator(mode="after")
    def _the_absence_states_stay_distinct(self) -> DictionaryBinding:
        """Keep the four resolution states from collapsing into one null.

        "Nobody has heard of this token", "several senses claim it equally" and "the binder never
        looked" are three different findings, and the first two are the ones a person can act on.
        The validator enforces the split and does not trust the caller, because the three states
        would otherwise collapse into one null and nothing would notice.
        """
        resolved = self.term_id is not None
        if (self.resolution_status is ResolutionStatus.RESOLVED) != resolved:
            message = (
                f"resolution status {self.resolution_status.value!r} and "
                f"{'a' if resolved else 'no'} bound term disagree"
            )
            raise ValueError(message)
        if resolved != (self.term is not None):
            # The name and the identifier are two views of one sense. The identifier hashes the
            # name, so one without the other is a row a rule can match on and not resolve, or
            # resolve and not match.
            message = "a bound term must carry both its identifier and its name"
            raise ValueError(message)
        if resolved and self.quality is None:
            # Every resolver path grades the match it made, and a rule refusing
            # one reached by stripping punctuation has to be able to tell a weak
            # grade from a missing one. An ungraded row would pass no floor and
            # look to a reader exactly like a match that failed one.
            message = "a bound term must say how it matched"
            raise ValueError(message)
        if self.resolution_status is ResolutionStatus.UNEXAMINED:
            if self.extraction_tier is not ExtractionTier.NOT_EXAMINED:
                message = "an unexamined binding must not claim a rung on the ladder"
                raise ValueError(message)
        elif self.extraction_tier is not ExtractionTier.SEMANTIC:
            message = "a binding that looked is a semantic reading and must say so"
            raise ValueError(message)
        if (
            self.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
            and not self.alternatives
        ):
            message = "a conflicting binding must name what it could not choose between"
            raise ValueError(message)
        if self.alternative_count != len(self.alternatives):
            message = "the alternative count and the alternatives disagree"
            raise ValueError(message)
        return self


# ---------------------------------------------------------------------------
# The Record: human decisions.
# ---------------------------------------------------------------------------


class RecordEventKind(StrEnum):
    """The kinds of decision a person can make."""

    CONFIRM = "confirm"
    REJECT = "reject"
    MERGE = "merge"
    SPLIT = "split"
    ASSIGN = "assign"
    DEFINE = "define"
    OVERRIDE = "override"
    ANNOTATE = "annotate"
    #: A person's words about a target, deciding nothing. A note is never the decision in force.
    #: A note written after a verdict leaves the verdict standing. A note on a row a person
    #: left without a verdict is kept and not dropped. ``annotate`` is a decision not to decide
    #: (``cannot_say``), which does displace an earlier verdict.
    NOTE = "note"
    #: Withdraws earlier events (schema 1.22), named in its payload
    #: (``{"withdrawn": [event_id, ...], "restored_to": ..., "previewed": ...}``). It is written
    #: only when a person goes back to an earlier run, never by the engine on its own. Nothing is
    #: deleted: a withdrawn event stays in the Record and in every export and is skipped on replay.
    #: A later withdrawal may name a withdrawal, which brings back what that one withdrew.
    #: Restoring a later run is itself a restore, and the Record only grows.
    WITHDRAW = "withdraw"


class RecordEvent(Record):
    """One human decision, appended and never rewritten.

    The Record replays decisions in order over every later inference, so a re-scan can never
    erase a decision.

    Testimony resolves only the question asked. Confirming that two folders are one session is a
    decision about that. It is never converted into a filesystem fact, and nothing here provides a
    field in which it could be.
    """

    event_id: str
    sequence: int
    kind: RecordEventKind
    actor: str
    occurred_at: str
    scope: Scope
    #: What the decision is about: an edge, an entry, a theme, a rule.
    target: str
    #: The run the decision was made against, so a decision can be read in the
    #: light of what the person was looking at when they made it.
    against_run_id: str
    #: Canonically serialized detail: the replacement type, the assigned value,
    #: the annotation text.
    payload: str = "{}"
    #: True when the decision's basis has vanished, such as an endpoint being
    #: deleted. An orphaned decision becomes visible rather than being silently
    #: dropped.
    orphaned: bool = False


# ---------------------------------------------------------------------------
# The engine's own files, as a census recognized them.
# ---------------------------------------------------------------------------


class RecognisedAs(StrEnum):
    """What of the engine's own a census found in a data pool: a directory, or, for the
    project a run writes, a file or folder of its own.

    Nothing is opened to tell: a directory is recognized from the names it holds, and the
    project a run writes by its folder's device and file number or its path.
    """

    #: A directory the running engine is imported from: its package, or the source
    #: directory an editable install names. Never indexed.
    INSTALLATION = "installation"
    #: Another project: a directory holding a project's identity file and its runs. It also holds
    #: either its settings and the pointer to its current run, or the rulebook, dictionary and
    #: record folders a project is made with, so a project that has never run is known too.
    PROJECT = "project"
    #: A directory holding a run's manifest beside its entry and run tables.
    RUN = "run"
    #: The project this run writes, known by its folder's device and file number or its path,
    #: never by its names. The census recognises its folder where that holds nothing else. It
    #: recognises each name the project writes where its folder is a root or holds a person's
    #: files too. Never indexed, whatever the settings say.
    THIS_PROJECT = "this_project"


class RecognisedPath(Record):
    """One path a census recognized as the engine's own, and what it did about it: a directory,
    or a file or folder of the project the run writes.

    The run record lists each path whether or not it was indexed, so a pool holding an earlier
    run's results says so, and entries missing from the Index have a stated reason and not a
    silent one.
    """

    root_id: str
    #: Relative to its root, with forward slashes, as an entry's path is.
    relative_path: str
    recognised_as: RecognisedAs
    #: Whether its contents were indexed anyway, as ordinary folders. Never true of
    #: the installation.
    indexed: bool = False


class ExclusionReason(StrEnum):
    """Why a census left a name out of the walk."""

    #: A name starting with a dot, while ``scan.include_hidden`` is off.
    HIDDEN = "hidden"
    #: A name or path matching one of ``scan.exclude_globs``.
    EXCLUDE_GLOB = "exclude_glob"
    #: Below ``scan.max_depth``.
    MAX_DEPTH = "max_depth"


class ExcludedNames(Record):
    """How many names of one kind a census left out of one root, and why.

    The census counts a name where it was left out, and never beneath it. An excluded folder is
    one excluded name of type directory. The walk does not descend into it. What it holds is
    neither indexed nor counted. The record says a folder was not walked and invents no figure
    for its contents. Counting beneath one would walk exactly what a person excluded to avoid
    walking, which on a home directory's hidden folders is most of the machine.

    The run says what it did not index. A census that skipped a name and kept no trace of it
    could not say that part of a drive, or a recycle bin, had never been seen. A person asking
    why the engine does not see their files would then get no answer from the run.
    """

    root_id: str
    reason: ExclusionReason
    #: The pattern that matched, for an exclusion by pattern. None otherwise.
    pattern: str | None = None
    #: What the name was, from the directory listing and without a further look.
    entry_type: EntryType
    count: Annotated[int, Field(ge=1)]


class MentionTally(Record):
    """How many mentions of one shape files of one extension made, in one run.

    The tally is kept by the mentioning file's extension and by shape. A run report can then say
    how many mentions came from scripts and notebooks, and how many from the include and import
    lines inside a bundled program. It needs no query over the observations.
    """

    #: The mentioning file's extension, lower-cased, empty where it has none.
    extension: str
    #: ``code_include``, ``absolute_foreign``, ``absolute_local``, ``relative``, ``bare``,
    #: or ``unclassified`` for a mention that an older engine recorded without a shape.
    shape: str
    #: Distinct paths, counted once per file and extractor.
    mentions: Annotated[int, Field(ge=0)]
    #: Files making at least one mention of this shape.
    files: Annotated[int, Field(ge=0)]


class PackInForce(Record):
    """One laboratory pack a run's vocabulary loaded.

    Reproducing a run means installing what it names. A pack's senses are part of the Dictionary
    the run consulted, and ``dictionary_hash`` says the composed vocabulary differed without
    saying why. The name and version say which pack, and the digest over its files says which
    copy.
    """

    name: str
    version: str
    digest: str


class ResolutionTally(Record):
    """What one inference's resolution made of the mentions of one shape, by extension.

    This is the exit measurement of reference resolution, kept in the run record: resolved,
    abstained, or outside the pool, by the mentioning file's extension and the mention's shape. A
    report has the figure without a query, and two runs' figures compare.
    """

    #: The mentioning file's extension, lower-cased, empty where it has none.
    extension: str
    #: The mention's shape as :class:`MentionTally` counts it.
    shape: str
    #: ``in_folder``, ``rooted`` or ``from_root`` where it resolved, and ``outside_pool``,
    #: ``several_matches``, ``no_match`` or ``code_include_not_resolved`` where it did not.
    outcome: str
    #: Distinct paths, counted once per file and extractor.
    mentions: Annotated[int, Field(ge=0)]
    #: Files making at least one mention with this outcome.
    files: Annotated[int, Field(ge=0)]


# ---------------------------------------------------------------------------
# The run record.
# ---------------------------------------------------------------------------


class WarningTally(Record):
    """Every warning of one kind a run raised, counted, with a bounded sample (1.22).

    A library can warn once per file, and a run over a large pool then held every message in
    memory before summarizing them. A tally keeps the count exactly and the words boundedly: the
    first message, the last, and at most a few between. So the record of a pathological run is
    the size of a healthy one's.
    """

    #: What raised it: ``python.warnings`` or a logger's name.
    source: str
    #: The kind, normalized so that one warning about many files is one signature: the warning's
    #: category and its message with numbers and quoted paths replaced by placeholders.
    signature: str
    count: Annotated[int, Field(ge=1)]
    first_message: str
    last_message: str
    #: At most a few messages between the first and the last, in the order they came.
    sample: tuple[str, ...] = ()


class CaseReadingStated(Record):
    """How a run read the spellings a capital divides (schema 1.22): the settings
    ``dictionary.case_reading`` held when the names were read.

    A partial rebind carries every name it does not read again with the reading its parent gave
    it. A run that read the carried names' spellings with another ratio would read one spelling
    two ways. The partial rebind compares this with its own and refuses.
    """

    enabled: bool
    whole_over_alone: Annotated[float, Field(gt=0.0)]
    unknown_whole: str


class RunRecord(Record):
    """Everything needed to explain, compare and reproduce one run.

    Every run records here everything that produced it: every parameter, every hash, every seed,
    every active component and every timing. The record is structured and not free text, so that
    runs can be queried against each other. The experiment register and the convergence indicator
    both need that.
    """

    run_id: str
    #: The run this one followed, where there is one. Runs are a time series.
    parent_run_id: str | None = None
    started_at: str
    ended_at: str | None = None

    engine_version: str
    id_scheme_version: str = ID_SCHEME_VERSION

    #: Which depth profile was requested: census, standard, deep, cluster.
    profile: str = "census"
    #: Work granted for content extraction, and work actually spent. The budget
    #: governs how hard the engine works to extract information from file
    #: contents, and nothing else. It can never cause a run to fail: an exhausted
    #: budget leaves a complete run with entities honestly marked unexamined.
    deepening_budget_granted_seconds: float | None = None
    deepening_budget_spent_seconds: float | None = None
    #: The wall clock one file's supervised reading was allowed: the base in seconds, and
    #: the seconds added per gigabyte of the file being read. ``None`` for a run that did
    #: no content work.
    #:
    #: The run records this because a ceiling decides what a run could not learn, and a killed
    #: reading is recorded as a content error, the outcome that says the file or the parser is at
    #: fault. A flat ceiling made being large indistinguishable from being broken. A scaled
    #: ceiling is a policy, and a policy a run was bound by belongs in the record of that run and
    #: not in whatever the settings say later.
    extraction_ceiling_seconds: tuple[float, float] | None = None

    #: Content hashes of the three interpretive inputs. Together with the Index
    #: they are what makes a run reproducible.
    rulebook_hash: str | None = None
    dictionary_hash: str | None = None
    config_hash: str | None = None

    #: Which optional components were configured, enabled and able to run.
    components: tuple[ComponentActivation, ...] = ()
    #: Every random seed used, so a permutation test reproduces exactly.
    seeds: tuple[tuple[str, int], ...] = ()
    #: Versions of every library whose behavior could affect the result.
    library_versions: tuple[tuple[str, str], ...] = ()
    platform: PlatformRecord | None = None

    #: Row counts per table, for validation before commit.
    counts: tuple[tuple[str, int], ...] = ()
    #: How many entries fell into each reuse decision.
    reuse_decisions: tuple[tuple[ReuseDecision, int], ...] = ()
    #: The engine's own files the census recognized by their layout, and whether it
    #: indexed each. Carried unchanged by a dive and an inference, which walk nothing,
    #: so the current run always says it.
    recognised: tuple[RecognisedPath, ...] = ()
    #: The names a census left out on purpose, by root, reason and type, counted where
    #: each was left out. Carried unchanged by a dive and an inference.
    excluded: tuple[ExcludedNames, ...] = ()
    #: The mentions the run's observations hold, by the mentioning file's extension and
    #: by shape. Written by a content pass, and carried by an inference over it.
    mentions: tuple[MentionTally, ...] = ()
    #: The laboratory packs the run's vocabulary loaded, in order. Empty where none was named,
    #: and where the run read no vocabulary at all.
    packs: tuple[PackInForce, ...] = ()
    #: What an inference's resolution made of those mentions. Empty where no rule in force draws
    #: on a resolved mention, and for a census or a content pass.
    resolutions: tuple[ResolutionTally, ...] = ()
    #: A partial rebind's spellings: the answered spellings whose names alone were read again,
    #: every other name's reading carried from the parent run. None for a whole rebind and for
    #: every other kind of run. A partial rebind over no spelling is ``()``.
    rebind_spellings: tuple[str, ...] | None = None

    # -- provenance and the Record's reach (1.22) ------------------------
    #: The engine's commit: the source revision of the package that wrote the run, with
    #: ``+dirty`` where the checkout held changes. The engine reads it at run time from the
    #: checkout or from the build's own stamp. ``None`` only where neither says.
    #: ``engine_version`` beside it is the package version, and every record carries its schema.
    engine_commit: str | None = None
    #: The Record watermark: the highest Record sequence this run incorporates. A decision with a
    #: higher sequence was made after the run read the Record and is not in it yet. That is how a
    #: page says "your answer is recorded and waiting for the next run" and does not guess.
    #: ``None`` for a run that read no Record. ``0`` for one that read an empty Record.
    record_watermark: int | None = None
    #: The durable job that produced the run, where one did (the job ledger's identifier).
    job_id: str | None = None
    #: The digest a content pass computed, and what chose it. The value is ``settings`` where a
    #: person set ``hash.mode``, ``profile`` where the depth profile did, and ``need`` where the
    #: engine hashed only what a comparison required. ``None`` for a run that computed no
    #: digest. A mode a person set is never silently overruled, and this says which source won.
    hash_mode: str | None = None
    hash_mode_set_by: str | None = None
    #: The years the run read as dates in names, first and last inclusive, as the user gave them
    #: or by default. A four-digit number outside them is a number.
    date_years: tuple[int, int] | None = None
    #: How the run read the spellings a capital divides, where it read names (a census or a
    #: rebind with the case reading on). ``None`` for a run that read no names, or one written
    #: before the field existed.
    case_reading: CaseReadingStated | None = None
    #: The name reader (tokenizer) the run read names with, as every component it wrote records
    #: it (``11``, with any reading a setting chose: ``11-six_digit_none``). This is a
    #: compatible addition within schema 1.22. ``None`` for a run that read no names, and for one
    #: written before the field existed, whose components still say.
    normalizer_version: str | None = None
    #: Warnings the run raised, counted by kind with a bounded sample (1.22).
    warnings: tuple[WarningTally, ...] = ()
    #: Whether an inference ran streamed by folder (a compatible addition within schema 1.22).
    #: Its subjects were assembled a chunk of whole folders at a time and not all at once, which
    #: publishes the same rows. ``None`` for every pass that is not an inference, and for an
    #: inference written before the field existed.
    streamed: bool | None = None
    #: Why it streamed or did not: the setting in force, and where ``auto`` decided, the
    #: projected peak against the machine's memory less the margin, both as read at run time.
    streaming_reason: str | None = None

    #: The publication invariants and how they fared. A failing invariant blocks
    #: the commit. The outcomes are recorded either way.
    invariants: tuple[InvariantOutcome, ...] = ()
    #: True once the run passed validation and its pointer was swapped in.
    published: bool = False
    #: Whether the content pass left files on a network share unhashed, and who chose (a
    #: compatible addition within schema 1.22). The value is ``settings`` where a person set
    #: ``hash.skip_remote_filesystems``, and ``profile`` where the depth's preset proposed it. A
    #: person's choice stands at every depth, as their ``hash.mode`` does. ``None`` for a run that
    #: is not a content pass, and for one written before the field.
    skip_remote_filesystems: bool | None = None
    skip_remote_filesystems_set_by: str | None = None
    #: Whether the pass counted what libraries said (a compatible addition within schema 1.22).
    #: True where ``warnings`` is a count, and False where the pass counts nothing. ``None`` on a
    #: run written before a run said so. A reader judges such a run by what only a dive states at
    #: 1.22 (``core.run_records.warnings_counted``).
    warnings_tallied: bool | None = None
    #: The content pass's clocks: the seconds the pass took on the wall clock, and the
    #: work-seconds it spent. Work-seconds are the sum of every file's own reading time, which at N
    #: workers is up to N times the wall. The third field is how many workers read files at once.
    #: ``None`` for a run that is not a content pass, and for one written before the fields
    #: existed.
    wall_seconds: float | None = None
    work_seconds: float | None = None
    workers: int | None = None


class QualityState(StrEnum):
    """Whether a quality figure holds a count, and if not, why not.

    The figure has three states kept apart, as absence is everywhere in the engine. A figure no
    pass has reached yet is not a figure the engine does not keep, and neither is a zero.
    """

    #: The figure was counted, exactly, from what the run stored. A zero is a count.
    COUNTED = "counted"
    #: The pass that wrote this run does not do what the figure counts: a census has read no
    #: file's content and drawn no conclusion. Nothing is said, in particular not zero.
    NOT_RUN = "not_run"
    #: The engine does not record what the figure would count, or the pass could not. The
    #: figure is listed so a table says so rather than leaving a column out.
    NOT_RECORDED = "not_recorded"


class QualityFigure(Record):
    """One quality figure of one run: a count with its denominator, a duration, or a peak memory.

    The figure holds only what the engine counts exactly from what it stored. Nothing is
    estimated. Each figure is named by its ``figure`` identifier in the catalog at
    ``ingence/quality``, which holds its label, its unit and its one-line method, written once. A
    breakdown (files by kind, claims by primitive and tier) is one row per ``facet``. A figure
    with no breakdown has the empty facet. A share is never stored. It is ``count`` over ``total``,
    and a reader shows both.

    The figure is not a claim and not an observation. It describes a run's tables, and the run's
    tables are all it is computed from, so a reader can recompute it from them and compare it
    with the stored row. The versions that wrote the run stay on the run record and are not
    copied here.
    """

    run_id: str
    #: The pass that wrote the run: ``census``, ``rebind``, ``dive``, ``infer`` or ``listing``.
    phase: str
    #: The catalog identifier, ``section.name`` (``read.files_read``).
    figure: str
    #: The breakdown value, or the empty string where the figure has none.
    facet: str = ""
    state: QualityState = QualityState.COUNTED
    #: The count, for a counted figure that counts things. ``None`` otherwise.
    count: Annotated[int, Field(ge=0)] | None = None
    #: What ``count`` is out of, where the figure has a whole it is part of. ``None`` where it
    #: has none (bytes found, parts recovered).
    total: Annotated[int, Field(ge=0)] | None = None
    #: A duration in nanoseconds, for a timed figure. ``None`` otherwise. Volatile: two runs of
    #: identical work differ in it, and a comparison of runs leaves it out.
    duration_ns: Annotated[int, Field(ge=0)] | None = None
    #: The peak resident memory of the engine's own process up to the end of the run, in bytes.
    #: ``None`` otherwise. Volatile like ``duration_ns``: a comparison of runs leaves it out.
    peak_bytes: Annotated[int, Field(ge=0)] | None = None
    #: Words the pass added to this row, where it had something to say (why it is not recorded).
    note: str = ""

    @model_validator(mode="after")
    def _a_counted_figure_counts_or_times(self) -> QualityFigure:
        """A counted figure holds a count, a duration or a peak. Any other state holds none."""
        held = any(value is not None for value in (self.count, self.duration_ns, self.peak_bytes))
        if (self.state is QualityState.COUNTED) != held:
            message = (
                f"quality figure {self.figure!r} is {self.state.value} and "
                f"{'holds' if held else 'lacks'} a count, a duration or a peak"
            )
            raise ValueError(message)
        if self.total is not None and self.count is None:
            message = f"quality figure {self.figure!r} has a total and no count"
            raise ValueError(message)
        return self


#: Every record type the engine persists, in the order the storage layout
#: declares them. Used by the schema exporter and by the layout agreement test,
#: so adding a record type without a table declaration fails a test rather than
#: being discovered in production.
PERSISTED_RECORDS: tuple[type[Record], ...] = (
    Root,
    Entry,
    Observation,
    Component,
    NameShape,
    DictionaryBinding,
    Claim,
    Candidate,
    Assertion,
    Rule,
    DictionaryEntry,
    RecordEvent,
    Theme,
    Role,
    ThemeKind,
    EquivalenceLevel,
    ScanError,
    RuleFailure,
    Finding,
    JournalEvent,
    QualityFigure,
    RunRecord,
)
