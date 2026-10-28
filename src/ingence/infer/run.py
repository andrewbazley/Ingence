"""The inference pass: evaluate the rules in force over a run, and publish what they came to.

This is the pass that evaluates the Rulebook's rules. It reads the current run and never the data
pool. It evaluates every classifying and grouping rule in force on every entry at the rule's level.
It then publishes a new, complete run carrying the Index forward with what the rules concluded: a
claim wherever a rule concluded and may license one, a failure wherever a rule could not decide, and
the rules in force themselves.

What a rule came to is counted whether or not it may publish. A defeasible rule that names nothing
which would defeat it licenses no claim, and it is still evaluated, so its outcomes and its
failures are recorded. A person deciding whether to write its defeat condition can see what it
would have concluded, and for a grouping rule how many classes it would have formed and how large
the largest was.

Classification runs before grouping, the order their effects give them. A grouping predicate is
handed an entry's records with the claims the run has already published about the entry. A grouping
rule keys every entry at its level, and the keys become classes through
:func:`~ingence.grouping.quotient.classes_of`. Each member of a class of two or more gets one claim
naming its class key. A class of one groups nothing and publishes nothing.

Proposing runs after grouping. A proposing rule draws its pairs within the index it names, a
grouping rule's claimed classes or the files of one folder, and each candidate carries every pair
constraint in force on its primitive, fired or not. Candidates are rewritten on every inference, as
claims are, and a subject a bound cut gets a claim saying so.

Derivations come before stages. The classifying phase's stage claims are the name's and the
format's own evidence, and no proposing rule reads one to form a pair or to direct one. Each
derivation is found and directed by its own evidence, or by time. What reads the derivations runs
after them: each file is handed the derivations proposed into it, with its sources' stages, before
the flagging phase, where a stage disagreeing with a derivation is raised for a person.

Flagging runs last. A flagging rule raises a finding about an entry or about a scope. A finding is
a record of its own and never a claim, and it cites records its subject carries. A scope is one
indexed root with every entry under it, which is what a fact about a region rather than about a
file is raised against. A flag reading a group is not
evaluable, because nothing assembles a class of a grouping. Findings are rewritten on every
inference.

Levels. A rule at level ``file`` reads every entry that is not a directory, which is what lets the
symbolic-link half of a placeholder rule fire. A rule at level ``folder`` reads directories. A rule
at level ``group`` reads classes, and no phase here hands one to a rule. The level ``root`` is the
fourth, added with the scope subject. It reads one region per indexed root and not any entry, and
only a flagging rule may declare it, because only a flag reads a scope. A scope-reading rule at any
other level is told which level the pass assembles, and is not handed a region it did not ask about.
Refusing it at load would refuse the three shipped folder flags, whose scope level the pass does not
assemble for them and which are not wrong.

This pass replaces what an earlier inference concluded. The claims the scan layer's own rules made
are carried unchanged. Every claim and failure a Rulebook rule licensed is recomputed from the
Index. That is what makes a rule switched off stop concluding on the next run, and not leave its
old conclusions behind.
"""

from __future__ import annotations

import contextlib
import functools
import json
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast

from ingence import __version__
from ingence.core.ids import ScopeKind
from ingence.core.invariants import GraphSnapshot, check_all
from ingence.core.registry import ComponentNotAvailableError
from ingence.core.relations import Primitive
from ingence.core.run_records import build_run_record, counting_warnings
from ingence.core.schema import (
    Assertion,
    Candidate,
    Claim,
    ComponentActivation,
    EntryKind,
    EntryType,
    FailureClass,
    Finding,
    FindingSubject,
    MentionTally,
    RecordEventKind,
    ResolutionTally,
    Root,
    Rule,
    RuleEffect,
    RuleFailure,
    RunRecord,
)
from ingence.core.tiers import DecisionTier, ResolutionStatus

# Aliased, because the pass holds the rules in force under that name.
from ingence.dictionary.laboratory_packs import in_force as packs_in_force
from ingence.dictionary.programs import program_layouts
from ingence.dictionary.recorded import recorded_origins
from ingence.grouping.lattice_words import write_reading
from ingence.grouping.quotient import classes_of
from ingence.infer.answered import (
    ClassRefusals,
    answer_reader,
    answers_for_pass,
    claim_class_reader,
    claims_spelling,
    refusal_defeaters,
    with_answered_copies,
)
from ingence.infer.candidates import ProposalTally, candidates_to_publish, propose
from ingence.infer.claims import (
    SharedDefeaters,
    claim_for,
    failure_for,
    finding_for,
    why_not_claim_ready,
)
from ingence.infer.evaluate import (
    evaluate_on_entry,
    key_on_entry,
    parameters_in_force,
    raise_on_entry,
    raise_on_scope,
)
from ingence.infer.folder_contents import (
    ACQUISITION_WRITER,
    MINIMUM_ALIKE_ITEMS,
    MINIMUM_ITEMS,
    MINIMUM_KEY_CHARACTERS,
    MOST_FOLDERS_A_DATE,
    broken_addresses,
    declared_copy_times,
    with_folder_contents,
)
from ingence.infer.records import (
    assemble_entry_records,
    assemble_scope_records,
    batched_rows,
    observations_of,
    tables_read,
)
from ingence.infer.references import (
    OUTCOMES,
    REFERENCES_KEY,
    SCRIPT_FILE_NAMES_KEY,
    PoolPaths,
    Resolution,
    mentions_of,
    prefix_mappings_in_force,
    resolve_all,
    tally_resolutions,
)
from ingence.infer.streaming import (
    ChunkedObservations,
    ChunkedSubjects,
    StreamingDecision,
    decide_streaming,
    folder_chunks,
    physical_memory,
    reads_observations,
)
from ingence.infer.subjects import Derivation, sibling_key, template_key
from ingence.jobs.provenance import current_job_id, record_watermark
from ingence.record.answers import rejected_by_a_person
from ingence.record.log import read_at
from ingence.record.workflow import workflows_in_force
from ingence.rulebook import project_rulebook
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.predicates.workflows import NEIGHBOUR_PREDICATES
from ingence.rulebook.registry import (
    CO_MENTION_INDEX,
    ENTRY_SHAPED,
    MENTION_INDEX,
    PARENT_INDEX,
    PREDICATES,
    PredicateNotEvaluableError,
    Reads,
    VocabularyRead,
    reads_name_slots,
    reads_of,
    vocabulary_of,
)
from ingence.rulebook.revision_5.workflows import WORKFLOW_PREDICATE
from ingence.scan.mention_shapes import SHAPES_KEY
from ingence.storage.carry import carry_run
from ingence.storage.catalog import census_facts, open_catalog
from ingence.storage.commit import (
    CommitError,
    build_manifest,
    commit_run,
    resolve_current_run,
    staging_path,
)
from ingence.storage.compact import compact_run
from ingence.storage.index_store import FragmentWriter, record_from_row
from ingence.storage.journal import open_journal
from ingence.storage.layout import TABLES, owned_path, table_for
from ingence.storage.views import view_from_row, view_of

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from ingence.core.project import Project
    from ingence.core.schema import Defeater, Entry, InvariantOutcome, Observation, RecordEvent
    from ingence.dictionary.store import DictionaryStore
    from ingence.infer.evaluate import Outcome
    from ingence.infer.subjects import (
        EntryRecords,
        GroupResolution,
        NamedAcquisition,
        ScopeRecords,
    )
    from ingence.record.answers import AnswersRead
    from ingence.record.workflow import Workflow
    from ingence.rulebook.registry import Predicate
    from ingence.rulebook.resolve import EffectiveRulebook
    from ingence.scan.cancel import CancellationToken
    from ingence.scan.progress import ProgressReporter
    from ingence.storage.catalog import Catalog
    from ingence.storage.journal import Journal, NullJournal

__all__ = [
    "SCAN_RULE_PREFIXES",
    "InferenceReport",
    "RuleTally",
    "applies_at",
    "rule_outcomes",
    "run_inference",
]

#: The rule identifiers the scan layer's own claims cite, which this pass carries and does not
#: recompute. The Rulebook declares them as ``enumerates`` rules, which the census and the cascade
#: run and this pass never evaluates.
SCAN_RULE_PREFIXES: tuple[str, ...] = ("census.", "cascade.")

#: The tables this pass writes afresh. Every other table is carried verbatim.
_REWRITTEN = frozenset(
    {
        "candidates",
        "claims",
        "findings",
        "rule_failures",
        "rules",
        "run",
        "journal_events",
        "quality_figures",
    }
)


@dataclass
class RuleTally:
    """What one rule came to across a run, counted by outcome."""

    rule_id: str
    rule_version: str
    evaluated: int = 0
    concluded: int = 0
    no_evidence: int = 0
    #: Abstained on an input present and unreadable, which is coverage's to fix.
    starved: int = 0
    unexamined: int = 0
    conflicting: int = 0
    claims: int = 0
    #: A flagging rule's findings. Zero for every other rule.
    findings: int = 0
    #: A grouping rule's classes of two or more, the entries in them, and the size of
    #: the largest. Zero for every other rule.
    classes: int = 0
    grouped: int = 0
    largest: int = 0
    #: Why the rule could not be evaluated at all, where it could not.
    not_evaluable: str | None = None
    #: Why the rule may not license a claim, where it may not.
    not_claim_ready: str | None = None

    def count(self, outcome: Outcome) -> None:
        """Add one outcome to the tally."""
        self.evaluated += 1
        if outcome.concluded:
            self.concluded += 1
        elif outcome.status is ResolutionStatus.EXAMINED_CONFLICTING:
            self.conflicting += 1
        elif outcome.status is ResolutionStatus.UNEXAMINED:
            self.unexamined += 1
        elif outcome.failure_class is FailureClass.COMPREHENSION:
            self.starved += 1
        else:
            self.no_evidence += 1


@dataclass
class InferenceReport:
    """What one inference pass did."""

    run_id: str
    entries: int = 0
    #: Of ``entries``, the parts a dive recovered from inside files. They are counted apart from the
    #: files and folders, so a person reading the count is not told four documents are forty.
    parts: int = 0
    claims: int = 0
    carried_claims: int = 0
    failures: int = 0
    #: The classifying rules, in the order they ran.
    tallies: list[RuleTally] = field(default_factory=list)
    #: The grouping rules, in the order they ran.
    groupings: list[RuleTally] = field(default_factory=list)
    #: The proposing rules, in the order they ran.
    proposals: list[ProposalTally] = field(default_factory=list)
    #: Candidates proposed across every proposing rule.
    candidates: int = 0
    #: The flagging rules, in the order they ran.
    flags: list[RuleTally] = field(default_factory=list)
    #: Findings raised across every flagging rule.
    findings: int = 0
    #: What resolution made of the run's mentions, where a rule in force draws on it: one row per
    #: mentioning extension, shape and outcome.
    resolutions: tuple[ResolutionTally, ...] = ()
    invariants: tuple[InvariantOutcome, ...] = ()
    #: True when the pass was cancelled and therefore published nothing.
    cancelled: bool = False
    #: Whether the pass streamed by folder, and why, as its run record says.
    streamed: bool = False
    streaming_reason: str = ""


def rule_outcomes(report: InferenceReport) -> dict[str, int]:
    """What every rule evaluation of a pass came to, summed over its classifying, grouping and
    flagging rules.

    ``evaluated`` is the number of times a rule was asked about one subject. Each evaluation is
    counted once: ``concluded``, or an abstention kept in its own state: ``unknown`` (examined,
    no evidence), ``unexamined``, ``conflicting`` (evidence both ways) or ``starved`` (an input
    present and unreadable). The four abstentions are never added together.
    """
    held = [*report.tallies, *report.groupings, *report.flags]
    return {
        "evaluated": sum(tally.evaluated for tally in held),
        "concluded": sum(tally.concluded for tally in held),
        "unknown": sum(tally.no_evidence for tally in held),
        "unexamined": sum(tally.unexamined for tally in held),
        "conflicting": sum(tally.conflicting for tally in held),
        "starved": sum(tally.starved for tally in held),
    }


@dataclass(frozen=True, slots=True)
class _Context:
    """What every rule in one pass is evaluated against."""

    predicates: Mapping[str, Predicate]
    shapes: Mapping[str, Reads]
    in_force: Mapping[str, Rule]
    run_id: str
    token: CancellationToken | None
    #: Every folder scope, keyed by the folder's entry, where a rule in force names a folder rule
    #: as its defeater, and every entry's parent, to walk up from a file to them.
    folders: Mapping[str, ScopeRecords] = field(default_factory=dict)
    parents: Mapping[str, str | None] = field(default_factory=dict)
    #: Each entry's own records, keyed by identifier, on the same terms. A rule about a folder
    #: names a defeater about that folder, and a scope carries its children and not itself, so the
    #: folder's own records are looked up here.
    holders: Mapping[str, EntryRecords] = field(default_factory=dict)
    #: What each folder defeater came to on each folder it was asked about, so a folder is
    #: read once however many files beneath it are asked about.
    raised: dict[str, dict[str, Outcome]] = field(default_factory=dict)
    #: Each defeating rule's sentence rendered once, and its unfired record shared.
    shared: SharedDefeaters = field(default_factory=SharedDefeaters)
    #: A person's answers on sets of copies and proposed pairs, read at the run's Record watermark,
    #: where a rule in force reads them; and the rule reading copies answers.
    answers: AnswersRead | None = None
    answer_reader: Rule | None = None
    #: A person's "Wrong" on a class of claims: the defeater on each claim of it, where the rule
    #: reading those answers is in force.
    class_refusals: ClassRefusals | None = None
    #: Where the pass tells how far it has come: each rule gone through counts once, of the total
    #: the pass set when it began. None where nobody is watching.
    reporter: ProgressReporter | None = None


def _counted(rules: Sequence[Rule], reporter: ProgressReporter | None) -> Iterator[Rule]:
    """The rules in order, each counted as gone through once the caller has finished with it
    (however it finished: a rule passed over counts as gone through)."""
    for rule in rules:
        yield rule
        if reporter is not None:
            reporter.advance(rules_run=1)


def applies_at(level: str, entry: Entry) -> bool:
    """Whether a rule declared at a level reads this entry.

    ``file`` is every entry that is not a directory: files, symbolic links, other filesystem
    objects and members promoted from containers. A level no phase here produces subjects for reads
    nothing, which is abstention by construction and not a guess about which entries it meant.
    """
    if level == "file":
        return entry.entry_type is not EntryType.DIRECTORY
    if level == "folder":
        return entry.entry_type is EntryType.DIRECTORY
    return False


@counting_warnings("inference")
def run_inference(
    project: Project,
    *,
    run_id: str,
    started_at: str,
    token: CancellationToken | None = None,
    rulebook: EffectiveRulebook | None = None,
    reporter: ProgressReporter | None = None,
) -> InferenceReport:
    """Evaluate the rules in force over the current run, phase by phase, and publish.

    ``rulebook`` is the Rulebook to evaluate. Where none is given it is the project's: the shipped
    Rulebook with the project's own revisions composed after it. ``reporter`` is told how many
    rules there will be when the pass knows them and counts each one gone through (``rules_run`` of
    ``rules_total``), for the landing page's first run. A cancelled pass publishes nothing and
    leaves the previous run current, as every other pass does.

    The pass closes whatever it opens for itself, however it ends. The catalog a streamed pass
    loads its chunks from is held by a stack this function closes.
    """
    with contextlib.ExitStack() as observing:
        return _run_inference(
            project,
            run_id=run_id,
            started_at=started_at,
            token=token,
            rulebook=rulebook,
            observing=observing,
            reporter=reporter,
        )


def _run_inference(
    project: Project,
    *,
    run_id: str,
    started_at: str,
    token: CancellationToken | None,
    rulebook: EffectiveRulebook | None,
    observing: contextlib.ExitStack,
    reporter: ProgressReporter | None = None,
) -> InferenceReport:
    """The pass itself; :func:`run_inference` holds what it opens."""
    report = InferenceReport(run_id=run_id)
    previous = resolve_current_run(project.path)
    if previous is None:
        message = "no run has been published; index before inferring"
        raise CommitError(message)
    # The Record watermark: read before anything here reads the Record.
    watermark = record_watermark(project.path)
    # Every read of the Record from here reads it at that watermark. An answer written while the
    # pass runs is the next run's.
    observing.enter_context(read_at(project.path, watermark))

    # The project's own staging, nothing on the way to it a link (``owned_path``).
    staging = owned_path(project.path, staging_path(project.path, run_id))
    staging.mkdir(parents=True, exist_ok=False)
    journal = open_journal(staging / "journal", run_id, enabled=project.settings.journal.enabled)

    load_builtins()
    book = project_rulebook(project.path) if rulebook is None else rulebook
    in_force = {rule.rule_id: rule for rule in book.rules}
    # The rule reading the workflows in force runs after the suggester, and never among the first
    # phase's classifying rules, which the suggester reads.
    by_workflow = [
        rule
        for rule in _of_effect(book.rules, RuleEffect.CLASSIFIES)
        if rule.predicate == WORKFLOW_PREDICATE
    ]
    classifying = [
        rule
        for rule in _of_effect(book.rules, RuleEffect.CLASSIFIES)
        if rule.predicate != WORKFLOW_PREDICATE
    ]
    grouping = _of_effect(book.rules, RuleEffect.GROUPS)
    proposing = _of_effect(book.rules, RuleEffect.PROPOSES)
    constraining = _of_effect(book.rules, RuleEffect.CONSTRAINT)
    flagging = _of_effect(book.rules, RuleEffect.FLAGS)
    if reporter is not None:
        # Every rule of the four phases that goes through a rule at a time; a constraint rule is
        # read by the proposing rules and is not gone through on its own.
        reporter.set(
            rules_total=len(classifying)
            + len(grouping)
            + len(proposing)
            + len(by_workflow)
            + len(flagging)
        )
    predicates, shapes, unavailable = _resolve_predicates(
        [*classifying, *grouping, *proposing, *by_workflow, *constraining, *flagging], in_force
    )
    folder_defeaters = sorted(
        {
            name
            for rule in book.rules
            for name in rule.defeated_by
            if name in in_force
            and shapes.get(name) is Reads.SCOPE
            and in_force[name].level == FOLDER_LEVEL
        }
    )
    # The Dictionary's program layouts are handed to the scopes where a rule reads a scope, or a
    # folder defeater does, that declares it reads them. Nobody else loads them.
    reads_layouts = any(
        VocabularyRead.PROGRAM_LAYOUTS in vocabulary_of(PREDICATES.declaration(rule.predicate))
        for rule in (
            *(rule for rule in flagging if shapes.get(rule.rule_id) is Reads.SCOPE),
            *(in_force[name] for name in folder_defeaters),
        )
    )
    # Read once and handed to every subject the later phases see. A workflow is what says where
    # data enters, so a rule asking whether a file is an observation or a derivation needs one.
    # A predicate is never given the project, because one that could reach it could write to it.
    declared = workflows_in_force(project.path)

    carry_run(previous, staging, rewritten=_REWRITTEN)

    claims_writer = FragmentWriter(table_for(Claim), staging / "claims")
    with open_catalog(project.path) as catalog:
        reads = set(shapes.values()) & ENTRY_SHAPED
        if any(rule.candidates_within is not None for rule in proposing):
            # A pair's ends are read whole: their bindings and what was concluded about them.
            reads.add(Reads.RECORDS)
        scoped = [rule for rule in flagging if shapes.get(rule.rule_id) is Reads.SCOPE]
        if scoped or folder_defeaters:
            # A scope's entries are read for what their names were cut into and what the
            # Dictionary made of each piece. Their observations are not read, because they are the
            # largest table a dive produces and memory is the figure to watch. A scope rule
            # needing one gets a line and a reason and not a default nobody chose.
            reads.add(Reads.BINDINGS)
        # The Dictionary is consulted only where a rule reads a name against its spellings, and a
        # run that consulted it records which one.
        vocabulary = (
            _vocabulary(project)
            if reads & {Reads.NAME_SPELLINGS, Reads.FORMAT_ORIGIN, Reads.FOLDER_CONTENTS}
            or reads_layouts
            else None
        )
        # Who writes each spelling, where a rule reads it on a file or counts a folder's
        # acquisitions by it. It is read once and handed to both.
        declared_origins = (
            format_origins(vocabulary)
            if vocabulary is not None and reads & {Reads.FORMAT_ORIGIN, Reads.FOLDER_CONTENTS}
            else None
        )
        # A declared workflow's entering types count as acquisitions. A folder's acquisitions are
        # counted by the spellings an instrument writes, and the rung where data enters a declared
        # workflow names more of them. They are keyed apart from every sense, so a rule reading a
        # sense's origins by its term never sees them.
        if declared_origins is not None:
            declared_origins = {
                **declared_origins,
                **{
                    f"workflow:{workflow.identifier}": dict.fromkeys(
                        workflow.entering_types, ACQUISITION_WRITER
                    )
                    for workflow in declared
                    if workflow.entering_types
                },
            }
        layouts = (
            program_layouts(vocabulary.sections) if reads_layouts and vocabulary is not None else ()
        )
        # Whether the pass streams by folder is decided from the setting, the pool's size and the
        # machine's memory as read now, and stated in the run record.
        counted = catalog.query("SELECT count(*) FROM entries WHERE NOT is_deleted").fetchone()
        streaming = decide_streaming(
            project.settings.infer,
            entries=int(counted[0]) if counted else 0,
            predicates={in_force[rule_id].predicate for rule_id in shapes},
            physical=physical_memory(),
        )
        # A rule reading a scope is handed the folder's own records for its defeaters, which a
        # streamed pass holds without observations. A rule naming a defeater that reads them runs
        # whole.
        observing_scopes = sorted(
            rule.rule_id
            for rule in flagging
            if shapes.get(rule.rule_id) is Reads.SCOPE
            and reads_observations(rule, shapes, in_force)
        )
        if streaming.streamed and observing_scopes:
            streaming = StreamingDecision(
                False,
                f"not streamed: {', '.join(observing_scopes)} reads a scope and names a defeater "
                "reading observations, so the pass runs whole",
            )
        report.streamed, report.streaming_reason = streaming.streamed, streaming.reason
        # Streamed, the observations are loaded a chunk at a time while a rule reads them.
        observed = streaming.streamed and "observations" in tables_read(reads)
        records = assemble_entry_records(
            catalog,
            with_observations=not observed,
            reads=reads,
            spellings=(
                None
                if vocabulary is None or Reads.NAME_SPELLINGS not in reads
                else _spellings(vocabulary)
            ),
            origins=declared_origins if Reads.FORMAT_ORIGIN in reads else None,
            # Where a rule in force keys on where a name's components stand, the name's own
            # slots are read. Otherwise one more table stays unread.
            with_slots=_keys_on_positions(grouping) or _flag_reads_slots(flagging),
            # What failed reading an entry, for a scope rule computing a failure rate over
            # a region. Loaded with the bindings and on the same terms: three things a
            # scope rule may read, decided once by which rules are in force.
            with_failures=bool(scoped),
            words_as=project.settings.groups.words_as,
        )
        # Only the scan's claims are read. They are the only ones carried, and a run inferred from
        # an earlier inference would otherwise load every claim that one published as a row before
        # dropping it. On real laboratory folders inferred again, that would be twenty rows for each
        # one carried, and gigabytes of memory. Both are read by Arrow batch. Each carried claim is
        # written as it is read and held as its view, and each edge is held as its view, so neither
        # table is ever held whole as rows or as records.
        published = _carried(catalog, claims_writer)
        edges: list[Assertion] = [
            view_from_row(row, Assertion)
            for row in batched_rows(catalog, "SELECT * FROM assertions ORDER BY edge_id")
        ]
        profile_row = catalog.query("SELECT profile FROM run").fetchone()
        # The mentions the Index holds are the run's it carries, and inference reads none of its
        # own, so the tally travels with them. A run written before the tally existed has none to
        # carry.
        run_columns = {row[0] for row in catalog.query("DESCRIBE SELECT * FROM run").fetchall()}
        mentions_row = (
            catalog.query("SELECT mentions_json FROM run").fetchone()
            if "mentions_json" in run_columns
            else None
        )
        # What the census left out and recognised travels the same way.
        left_out, recognised = census_facts(catalog)
        # A mention is resolved only where a rule in force draws on the resolution, and then once
        # for the whole run, from the Index alone: the two keys a mention is recorded under, and
        # each root's record, whose case label decides how a name is compared. Nothing is opened.
        # The Index is the whole search space.
        resolving = any(
            rule.candidates_within in (MENTION_INDEX, CO_MENTION_INDEX) for rule in proposing
        )
        mention_rows = (
            catalog.query(
                "SELECT observation_id, entry_id, observer, key, value_structure "
                "FROM observations WHERE key IN (?, ?, ?) ORDER BY observation_id",
                [REFERENCES_KEY, SHAPES_KEY, SCRIPT_FILE_NAMES_KEY],
            ).fetchall()
            if resolving
            else []
        )
        root_records = (
            {
                str(row["root_id"]): record_from_row(row, Root)
                for row in _rows(catalog.query("SELECT * FROM roots ORDER BY root_id"))
            }
            if resolving
            else {}
        )

    # Proximity is handed over only where a rule in force reads it: each file beside what its
    # folder and the folders beside it hold, as keys shared per folder.
    if Reads.ENTRY_AMONG_SIBLINGS in set(shapes.values()) or any(
        rule.predicate in NEIGHBOUR_PREDICATES for rule in in_force.values()
    ):
        records = _with_neighbours(records)

    # What each folder holds is counted only where a rule in force reads it: acquisitions by the
    # Dictionary's declared writer, and files named for them, per folder.
    if Reads.FOLDER_CONTENTS in set(shapes.values()):
        with open_catalog(project.path) as catalog:
            copies = declared_copy_times(catalog)
            unanswered = broken_addresses(catalog)
        records = with_folder_contents(
            records,
            declared_origins or {},
            minimum_key_characters=_minimum_key_characters(in_force, shapes),
            minimum_items=_minimum_items(in_force, shapes),
            minimum_alike_items=_minimum_items(in_force, shapes, "minimum_alike_items"),
            most_folders_a_date=_minimum_items(in_force, shapes, "most_folders_a_date"),
            declared_copies=copies,
            broken=unanswered,
        )

    # An archive's link is matched to the real file it stands for, from names and link text alone,
    # only where a rule in force reads it.
    if Reads.ARCHIVED_COPY in set(shapes.values()):
        from ingence.grouping.archive_links import declared_target_sizes, with_archived_copies

        # A listing's declared size of the file behind a listed link.
        with open_catalog(project.path) as catalog:
            sizes = declared_target_sizes(catalog)
        records = with_archived_copies(records, declared_sizes=sizes)

    # The dates a file draws from the folders above it, where a rule reads them.
    if Reads.NAME_DATES in set(shapes.values()):
        from ingence.infer.name_dates import with_folder_dates

        records = with_folder_dates(records)

    # A person's answer on a folder of miscellaneous files is marked on what the folder holds,
    # where that is counted.
    if Reads.FOLDER_CONTENTS in set(shapes.values()):
        records = _with_miscellaneous_answers(records, project.path)

    # A folder a person said is not a program is marked from the Record, so the rule raising
    # installed software leaves it in the data's scope.
    records = _with_folders_not_programs(records, project.path)

    # A folder rule may defeat a claim about anything beneath the folder it raised. So the folder
    # scopes are assembled before the first phase, from the same records, where some rule in force
    # names such a defeater, and not otherwise. They are assembled without their observations,
    # whole or streamed: a streamed pass holds none for the pool, and a scope rule is handed the
    # same records either way.
    regional = _without_observations(records) if folder_defeaters else ()
    context = _Context(
        predicates=predicates,
        shapes=shapes,
        in_force=in_force,
        run_id=run_id,
        token=token,
        folders=(
            {
                scope.scope.value: scope
                for scope in assemble_scope_records(regional, layouts=layouts)
                if scope.scope.kind is ScopeKind.FOLDER and scope.scope.value is not None
            }
            if folder_defeaters
            else {}
        ),
        parents=(
            {entry.entry.entry_id: entry.entry.parent_entry_id for entry in records}
            if folder_defeaters
            else {}
        ),
        holders=({entry.entry.entry_id: entry for entry in regional} if folder_defeaters else {}),
        # A person's answers on copies and pairs, at the run's watermark.
        answers=answers_for_pass(
            project.path,
            watermark,
            reads_copies=answer_reader(grouping) is not None
            or claim_class_reader(flagging) is not None,
            proposes=bool(proposing),
        ),
        answer_reader=answer_reader(grouping),
        reporter=reporter,
    )
    # A person's answer on a class of claims, read with the rest, at the watermark.
    context = replace(
        context, class_refusals=ClassRefusals(context.answers, claim_class_reader(flagging))
    )
    del regional

    # A streamed pass hands a rule reading observations the pool a chunk at a time: whole folders,
    # each chunk's observations loaded as the rule reaches it and let go after.
    load: Callable[[Sequence[str]], Mapping[str, Sequence[Observation]]] | None = None
    chunked: ChunkedObservations | None = None
    if observed:
        opened = observing.enter_context(open_catalog(project.path))
        load = functools.partial(observations_of, opened)
        with_any = {
            str(row[0])
            for row in opened.query("SELECT DISTINCT entry_id FROM observations").fetchall()
        }
        having = [
            position for position, held in enumerate(records) if held.entry.entry_id in with_any
        ]
        chunked = ChunkedObservations(
            folder_chunks(records, project.settings.infer.entries_per_chunk, having),
            load,
            ids=tuple(held.entry.entry_id for held in records),
        )
        del with_any, having

    def handed(rule: Rule, held: Sequence[EntryRecords]) -> Sequence[EntryRecords]:
        """The subjects one rule is handed: the pool, or streamed, the pool a chunk at a time."""
        if chunked is None or not reads_observations(rule, shapes, in_force):
            return held
        return cast("Sequence[EntryRecords]", ChunkedSubjects(held, chunked))

    report.entries = len(records)
    report.parts = sum(1 for held in records if held.entry.entry_kind is EntryKind.DERIVED)
    report.carried_claims = len(published)

    failures_writer = FragmentWriter(table_for(RuleFailure), staging / "rule_failures")
    rules_writer = FragmentWriter(table_for(Rule), staging / "rules")
    candidates_writer = FragmentWriter(table_for(Candidate), staging / "candidates")
    findings_writer = FragmentWriter(table_for(Finding), staging / "findings")
    for rule in book.rules:
        rules_writer.add(rule, now=time.monotonic())

    journal.record(
        "infer",
        "run.started",
        run_id=run_id,
        rules=len(classifying) + len(grouping),
        entries=len(records),
    )
    # Which words the run makes its groups of is said in a file of the run's own directory, which
    # the groups pages read and a dive carries with the claims. It is not in the run's record, so an
    # earlier build still reads that record unchanged. It is not in the journal either.
    write_reading(staging, project.settings.groups.words_as)
    phases: tuple[
        tuple[
            Sequence[Rule],
            list[RuleTally],
            Callable[..., tuple[list[Claim], list[RuleFailure], bool]],
        ],
        ...,
    ] = (
        (classifying, report.tallies, _classify),
        (grouping, report.groupings, _group),
    )
    # Each file's experiment folder, where a script's bare file names are looked for.
    experiment_of: dict[str, str] = {}
    named_from_files: dict[str, NamedAcquisition] = {}
    for phase, (rules, tallies, evaluate) in enumerate(phases):
        if report.cancelled:
            break
        # The rule reading a person's copies answers runs after the rules forming copies. It is
        # handed the members of the sets they claimed in this pass.
        rules = sorted(rules, key=lambda rule: shapes.get(rule.rule_id) is Reads.ANSWERED_COPIES)
        # A later phase reads what the earlier ones published about an entry.
        subjects = records if phase == 0 else _with_claims(records, published, declared)
        # Where each file lies in the experiment above it, for a grouping rule comparing names
        # within one, once the classifying rules have said which are experiments.
        if phase == 1:
            subjects, experiment_of = _placed_in_experiments(
                subjects, rules, in_force, resolving=resolving, shapes=shapes
            )
            # A file named for another file's whole name keeps that source for the proposing
            # phase. The rest are made from the acquisition their group is keyed on.
            named_from_files = {
                held.entry.entry_id: held.named_acquisition
                for held in subjects
                if held.named_acquisition is not None
                and (
                    held.named_acquisition.source
                    or held.named_acquisition.annotates
                    or held.named_acquisition.levels_up
                )
            }
        # An approved group of experiments is handed to the rules reading it, once the classifying
        # rules have said which folders are experiments.
        if phase == 1 and any(shapes.get(rule.rule_id) is Reads.APPROVED_GROUP for rule in rules):
            subjects = _with_approved_groups(subjects, project.path, in_force, rules, shapes)
        # A core set of terms a person approved is resolved for every experiment sharing it, once
        # the experiments are known. The flag phase reads it too.
        if phase == 1:
            by_core = _group_resolutions(subjects, project.path, in_force, journal)
            if by_core:
                subjects = _with_resolutions(subjects, by_core)
                records = _with_resolutions(records, by_core)
            declined = _declined_groups(project.path, in_force, subjects)
            if declined:
                records = tuple(
                    replace(held, declined_groups=declined)
                    if held.entry.entry_type is EntryType.DIRECTORY
                    else held
                    for held in records
                )
        for rule in _counted(rules, reporter):
            tally = RuleTally(rule_id=rule.rule_id, rule_version=rule.version)
            tallies.append(tally)
            if rule.rule_id in unavailable:
                tally.not_evaluable = unavailable[rule.rule_id]
                journal.record(
                    "infer", "rule.not_evaluable", rule_id=rule.rule_id, reason=tally.not_evaluable
                )
                continue
            tally.not_claim_ready = why_not_claim_ready(rule, in_force)
            given = (
                with_answered_copies(subjects, published, context.answers)
                if shapes.get(rule.rule_id) is Reads.ANSWERED_COPIES
                else subjects
            )
            try:
                rule_claims, rule_failures, cancelled = evaluate(
                    rule, handed(rule, given), tally=tally, context=context
                )
            except PredicateNotEvaluableError as exc:
                tallies[-1] = RuleTally(
                    rule_id=rule.rule_id, rule_version=rule.version, not_evaluable=str(exc)
                )
                journal.record("infer", "rule.not_evaluable", rule_id=rule.rule_id, reason=str(exc))
                continue
            if cancelled:
                report.cancelled = True
                break
            claims_writer.extend(rule_claims, now=time.monotonic())
            for failure in rule_failures:
                failures_writer.add(failure, now=time.monotonic())
            published.extend(view_of(claim) for claim in rule_claims)
            tally.claims = len(rule_claims)
            report.claims += len(rule_claims)
            report.failures += len(rule_failures)
            journal.record(
                "infer",
                "rule.evaluated",
                rule_id=rule.rule_id,
                evaluated=tally.evaluated,
                concluded=tally.concluded,
                no_evidence=tally.no_evidence,
                starved=tally.starved,
                unexamined=tally.unexamined,
                conflicting=tally.conflicting,
                classes=tally.classes,
                grouped=tally.grouped,
                largest=tally.largest,
                claims=tally.claims,
                not_claim_ready=tally.not_claim_ready or "",
            )

    mentioned: dict[str, tuple[Resolution, ...]] | None = None
    if resolving and not report.cancelled:
        pool = PoolPaths.of(
            (entry_records.entry for entry_records in records),
            root_records,
            prefix_mappings_in_force(project.path),
            experiments=experiment_of,
        )
        resolved = resolve_all(mentions_of(mention_rows), pool)
        grouped: dict[str, list[Resolution]] = {}
        for resolution in resolved:
            grouped.setdefault(resolution.mention.entry_id, []).append(resolution)
        mentioned = {entry_id: tuple(held) for entry_id, held in grouped.items()}
        extensions = {
            entry_records.entry.entry_id: (entry_records.entry.extension or "").casefold()
            for entry_records in records
        }
        report.resolutions = tuple(
            ResolutionTally(
                extension=extension, shape=shape, outcome=outcome, mentions=count, files=files
            )
            for extension, shape, outcome, count, files in tally_resolutions(resolved, extensions)
        )
        journal.record(
            "infer",
            "mentions.resolved",
            mentions=len(resolved),
            outcomes={
                outcome: sum(1 for resolution in resolved if resolution.outcome == outcome)
                for outcome in OUTCOMES
            },
        )

    # Each derivation no constraint or person refused, as (candidate, rule, source, target).
    derivations: list[tuple[str, str, str, str]] = []
    if not report.cancelled:
        if named_from_files:
            from ingence.infer.experiment_places import with_acquisitions_named

            records = with_acquisitions_named(records, named_from_files)
        proposed_over = _with_claims(records, published, declared)
        if load is not None:
            # Streamed, a pair's two ends are read whole: each end drawn within a class of an index
            # a proposing rule names is handed its observations, and nothing else is.
            proposed_over = _observed(
                proposed_over,
                _proposal_ends(proposing, published, mentioned),
                load,
                project.settings.infer.entries_per_chunk,
            )
        _propose(
            proposing,
            proposed_over,
            report=report,
            published=published,
            edges=edges,
            constraining=constraining,
            predicates=predicates,
            shapes=shapes,
            in_force=in_force,
            unavailable=unavailable,
            writers=(candidates_writer, claims_writer),
            journal=journal,
            context=context,
            mentioned=mentioned,
            found=derivations,
        )
        # The pairs' subjects are let go before the flags' are made.
        proposed_over = ()
        # What reads the derivations runs after them: each file is handed the derivations proposed
        # into it, with its sources' stages, for the flagging phase.
        if derivations:
            records = _with_derivations(records, derivations, published)
        derivations = []

    # After proposing, the suggester runs, deterministic, from the results of the classifying,
    # grouping and proposing phases. Then the rule places each file by the workflows in force (the
    # declared ones, and each suggestion no declaration replaces and no refusal names). Nothing
    # feeds back from a readout: the suggestion is written beside the claims, for the next run to
    # read as a suggestion again. With ``infer.workflows`` off, the declared workflows alone are
    # read.
    applied: tuple[Workflow, ...] = declared
    placed_in: dict[str, str] = dict(experiment_of)
    if not report.cancelled:
        applied, placed_in = _suggest_workflows(
            project, records, published, declared, experiment_of, in_force, staging, journal
        )
    if not report.cancelled and by_workflow:
        from ingence.infer.experiment_places import with_experiment_places
        from ingence.infer.subjects import ExperimentPlace

        # Each file's experiment travels with it. A suggested workflow is read only inside the
        # experiments it was counted from, never project-wide.
        staged = with_experiment_places(
            _with_claims(records, published, applied),
            {entry_id: ExperimentPlace(experiment=unit) for entry_id, unit in placed_in.items()},
        )
        for rule in _counted(by_workflow, reporter):
            tally = RuleTally(rule_id=rule.rule_id, rule_version=rule.version)
            report.tallies.append(tally)
            if rule.rule_id in unavailable:
                tally.not_evaluable = unavailable[rule.rule_id]
                journal.record(
                    "infer", "rule.not_evaluable", rule_id=rule.rule_id, reason=tally.not_evaluable
                )
                continue
            tally.not_claim_ready = why_not_claim_ready(rule, in_force)
            try:
                rule_claims, rule_failures, cancelled = _classify(
                    rule, staged, tally=tally, context=context
                )
            except PredicateNotEvaluableError as exc:
                report.tallies[-1] = RuleTally(
                    rule_id=rule.rule_id, rule_version=rule.version, not_evaluable=str(exc)
                )
                journal.record("infer", "rule.not_evaluable", rule_id=rule.rule_id, reason=str(exc))
                continue
            if cancelled:
                report.cancelled = True
                break
            claims_writer.extend(rule_claims, now=time.monotonic())
            for failure in rule_failures:
                failures_writer.add(failure, now=time.monotonic())
            published.extend(view_of(claim) for claim in rule_claims)
            tally.claims = len(rule_claims)
            report.claims += len(rule_claims)
            report.failures += len(rule_failures)
            journal.record(
                "infer",
                "rule.evaluated",
                rule_id=rule.rule_id,
                evaluated=tally.evaluated,
                concluded=tally.concluded,
                # Each abstention in its own state, as every other rule's evaluation journals it.
                no_evidence=tally.no_evidence,
                starved=tally.starved,
                unexamined=tally.unexamined,
                conflicting=tally.conflicting,
                claims=tally.claims,
                not_claim_ready=tally.not_claim_ready or "",
            )
        staged = ()

    # The last phase's subjects are let go before the flags' are made.
    subjects = given = ()
    if not report.cancelled:
        flagged = _with_claims(records, published, applied)
        # Every domain section loads, so no scope is handed the spellings of packs not loaded. The
        # folder profile supersedes the rule that proposed a pack.
        _flag(
            flagging,
            flagged,
            scopes=(
                assemble_scope_records(_without_observations(flagged), layouts=layouts)
                if scoped
                else ()
            ),
            report=report,
            unavailable=unavailable,
            writers=(findings_writer, failures_writer),
            journal=journal,
            context=context,
            handed=handed,
        )

    run_writer = FragmentWriter(table_for(RunRecord), staging / "run")
    run_writer.add(
        build_run_record(
            run_id=run_id,
            parent_run_id=previous.name,
            started_at=started_at,
            # The Index this run carries was built under the previous run's depth
            # profile, and inference chose none of its own.
            profile=str(profile_row[0]) if profile_row else "census",
            settings=project.settings,
            project=project,
            mentions=tuple(
                MentionTally.model_validate(item)
                for item in (json.loads(mentions_row[0]) if mentions_row else [])
            ),
            excluded=left_out,
            recognised=recognised,
            resolutions=report.resolutions,
            components=_predicate_snapshot(),
            rulebook_hash=book.digest,
            dictionary_hash=None if vocabulary is None else vocabulary.fingerprint(),
            packs=packs_in_force(vocabulary),
            record_watermark=watermark,
            job_id=current_job_id(),
            streamed=streaming.streamed,
            streaming_reason=streaming.reason,
            published=not report.cancelled,
        ),
        now=time.monotonic(),
    )
    for writer in (
        claims_writer,
        failures_writer,
        rules_writer,
        candidates_writer,
        findings_writer,
        run_writer,
    ):
        writer.close(now=time.monotonic())

    owner = {
        identifier: entry_records.entry.entry_id
        for entry_records in records
        for identifier in _record_identifiers(entry_records)
    }
    if observed:
        # Streamed, the subjects never held every observation, so their owners are read here.
        with open_catalog(project.path) as catalog:
            owner.update(
                (str(observation_id), str(entry_id))
                for observation_id, entry_id in catalog.query(
                    "SELECT observation_id, entry_id FROM observations WHERE entry_id IN "
                    "(SELECT entry_id FROM entries WHERE NOT is_deleted)"
                ).fetchall()
            )
    report.invariants = check_all(
        GraphSnapshot(assertions=tuple(edges), claims=tuple(published), observation_owner=owner)
    )

    observing.close()
    if report.cancelled:
        journal.record("infer", "run.abandoned", reason=token.reason if token else "cancelled")
        journal.close()
        return report
    journal.record(
        "infer",
        "run.finished",
        claims=report.claims,
        failures=report.failures,
        findings=report.findings,
    )
    journal.close()

    # Nothing the pass assembled outlives it into compaction. The subjects, the claims, the edges
    # and the predicates built for this pass, one of which keeps the pool it was handed, are
    # released before the run's tables are merged. The merge's own memory is then added to what a
    # pass leaves and not to everything it held.
    records = subjects = flagged = proposed_over = given = ()
    published = []
    edges = []
    rule_claims = []
    mention_rows = []
    vocabulary = None
    del context, predicates, owner
    if mentioned is not None:
        del pool, resolved, grouped
    mentioned = None
    for name in sorted({rule.predicate for rule in book.rules}):
        PREDICATES.release(name)

    load = None
    chunked = None
    rows = compact_run(staging, TABLES)
    # The run's quality figures, counted from the tables just compacted, with the counts of rule
    # outcomes only this pass holds.
    from ingence.quality.publish import write_quality

    # The summaries that read the Record (themes' names, the design grid's settings and answers)
    # read it at this run's watermark, as the pass did. A decision written while the run was going
    # changes nothing they state.
    with read_at(project.path, watermark):
        rows["quality_figures"] = write_quality(
            project.path,
            staging,
            run_id=run_id,
            phase="infer",
            extras={"rule_outcomes": rule_outcomes(report)},
        )
    manifest = build_manifest(
        staging,
        run_id=run_id,
        engine_version=__version__,
        created_at=started_at,
        counts=tuple(sorted(rows.items())),
        invariants=report.invariants,
    )
    commit_run(project.path, manifest=manifest, updated_at=started_at)
    return report


def _of_effect(rules: Iterable[Rule], effect: RuleEffect) -> list[Rule]:
    """Return the rules of one effect, in canonical order."""
    return sorted((rule for rule in rules if rule.effect is effect), key=lambda rule: rule.rule_id)


def _vocabulary(project: Project) -> DictionaryStore:
    """Return the Dictionary a rule reading a name against its spellings is handed.

    This is the same store the census binds names with: what ships, plus what the project's Record
    says a person decided. Reading a different one here would let a rule test a name against
    spellings no binding was ever made from. The function imports it inside, so an inference whose
    rules read no spelling never loads it.
    """
    from ingence.dictionary.store import DictionaryStore

    return DictionaryStore.for_project(project.path)


def _spellings(vocabulary: DictionaryStore) -> dict[str, tuple[str, ...]]:
    """Return every spelling of each sense, by term, in canonical order."""
    spelled: dict[str, set[str]] = {}
    for sense in vocabulary.senses():
        spelled.setdefault(sense.term, set()).update(sense.surfaces)
    return {term: tuple(sorted(surfaces)) for term, surfaces in sorted(spelled.items())}


def format_origins(vocabulary: DictionaryStore) -> dict[str, dict[str, str]]:
    """Who writes each spelling of each format: the senses' own origins, with the file-types
    table laid over them where one ships. The one place that choice is made is
    :func:`ingence.dictionary.recorded.recorded_origins`. This function keeps its old name for
    callers."""
    return recorded_origins(vocabulary)


def _resolve_predicates(
    rules: Sequence[Rule], in_force: Mapping[str, Rule]
) -> tuple[dict[str, Predicate], dict[str, Reads], dict[str, str]]:
    """Build the predicate behind every rule to evaluate and every defeater they name.

    Returns the predicates, what each reads, and why any could not be built, keyed by rule. A
    defeater that is not in force is left out, and the rule naming it is reported as not ready to
    license a claim and is not evaluated against nothing.
    """
    needed = {rule.rule_id: rule for rule in rules}
    for rule in rules:
        for name in rule.defeated_by:
            if name in in_force:
                needed[name] = in_force[name]
    predicates: dict[str, Predicate] = {}
    shapes: dict[str, Reads] = {}
    unavailable: dict[str, str] = {}
    for rule_id, rule in sorted(needed.items()):
        shapes[rule_id] = reads_of(PREDICATES.declaration(rule.predicate))
        try:
            predicates[rule_id] = PREDICATES.get(rule.predicate)
        except ComponentNotAvailableError as exc:
            unavailable[rule_id] = str(exc)
    return predicates, shapes, unavailable


def _classify(
    rule: Rule, records: Sequence[EntryRecords], *, tally: RuleTally, context: _Context
) -> tuple[list[Claim], list[RuleFailure], bool]:
    """Evaluate one classifying rule on every entry at its level, holding what it publishes.

    The pass holds the results and does not write them as it goes, so a rule that turns out not to
    be evaluable part of the way through publishes nothing and not a prefix of its conclusions.
    """
    predicate = context.predicates[rule.rule_id]
    shape = context.shapes[rule.rule_id]
    claims: list[Claim] = []
    failures: list[RuleFailure] = []
    for entry_records in records:
        if context.token is not None and context.token.cancelled:
            return claims, failures, True
        if not applies_at(rule.level, entry_records.entry):
            continue
        outcome = evaluate_on_entry(rule, predicate, entry_records, reads=shape)
        tally.count(outcome)
        if outcome.failure_class is FailureClass.INTERPRETATION:
            failures.append(failure_for(rule, outcome, run_id=context.run_id))
            continue
        if not outcome.concluded or tally.not_claim_ready is not None:
            continue
        defeaters = _defeaters(rule, entry_records, context)
        claims.extend(
            claim_for(
                rule,
                outcome,
                value,
                run_id=context.run_id,
                defeaters=(
                    *defeaters,
                    *_class_refusal(rule, value, entry_records, defeaters, context),
                ),
            )
            for value in outcome.values
        )
    return claims, failures, False


def _class_refusal(
    rule: Rule,
    value: str,
    entry_records: EntryRecords,
    defeaters: Sequence[Defeater],
    context: _Context,
) -> tuple[Defeater, ...]:
    """A person's "Wrong" on the class this claim would join, where the claim is a member.

    Membership is the one definition (``review/claim_classes.belongs``): outside every folder
    raised as an installed program, with no other defeater fired, which the sheet and the handle
    read from the published run. The function is asked only of a refused class.
    """
    from ingence.review.claim_classes import belongs

    refusals = context.class_refusals
    if refusals is None or not refusals.refused(rule, value):
        return ()
    fired = [defeater.rule_id for defeater in defeaters if defeater.fired]
    beneath = _beneath_a_program(entry_records.entry, context)
    if not belongs(fired, refusals.readers, beneath_a_program=beneath):
        return ()
    return refusals.on(rule, value, entry_records.entry.entry_id)


def _beneath_a_program(entry: Entry, context: _Context) -> bool:
    """Whether an entry lies in or beneath a folder a rule in force raises as an installed program.

    The regions the review pages leave out (``pages._regions``), found in the pass by the same rule
    on the same folders. Where no folder scope was assembled (no rule names a folder defeater),
    none is found.
    """
    from ingence.review.claim_classes import PROGRAM_PREDICATE

    for rule in sorted(context.in_force.values(), key=lambda held: held.rule_id):
        if rule.predicate != PROGRAM_PREDICATE or rule.rule_id not in context.predicates:
            continue
        if _raised_above(rule, context.predicates[rule.rule_id], entry, context) is not None:
            return True
    return False


def _group(
    rule: Rule, records: Sequence[EntryRecords], *, tally: RuleTally, context: _Context
) -> tuple[list[Claim], list[RuleFailure], bool]:
    """Key every entry at a grouping rule's level, form its classes, and claim members into them.

    Classes are counted whether or not the rule may publish. A member of a class of two or more
    gets one claim naming the class key, with the rule's defeaters evaluated on that member. An
    entry alone under its key gets nothing.
    """
    predicate = context.predicates[rule.rule_id]
    shape = context.shapes[rule.rule_id]
    # What is kept for each keyed entry until the classes form: its defeaters, evaluated now,
    # where one reads observations; otherwise its records without them, which is all its defeaters
    # read. Never an entry's observations, which a streamed pass holds a chunk at a time.
    observing = any(
        context.shapes.get(name) in (Reads.OBSERVATIONS, Reads.RECORDS, Reads.ENTRY_OBSERVATIONS)
        for name in rule.defeated_by
        if name in context.in_force
    )
    keyed: list[tuple[str, tuple[EntryRecords | tuple[Defeater, ...] | None, Outcome]]] = []
    failures: list[RuleFailure] = []
    for entry_records in records:
        if context.token is not None and context.token.cancelled:
            return [], failures, True
        if not applies_at(rule.level, entry_records.entry):
            continue
        outcome = key_on_entry(rule, predicate, entry_records, reads=shape)
        tally.count(outcome)
        if outcome.failure_class is FailureClass.INTERPRETATION:
            failures.append(failure_for(rule, outcome, run_id=context.run_id))
        elif outcome.concluded:
            held: EntryRecords | tuple[Defeater, ...] | None
            if tally.not_claim_ready is not None:
                held = None
            elif observing:
                held = _defeaters(rule, entry_records, context)
            else:
                held = (
                    replace(entry_records, observations=())
                    if entry_records.observations
                    else entry_records
                )
            keyed.append((outcome.values[0], (held, outcome)))
    classes = classes_of(keyed)
    tally.classes = len(classes)
    tally.grouped = sum(len(members) for members in classes.values())
    tally.largest = max((len(members) for members in classes.values()), default=0)
    claims: list[Claim] = []
    # A set a person answered is unrelated: their answer, fired, on each claim.
    refused = refusal_defeaters(rule, classes, context.answers, context.answer_reader)
    if tally.not_claim_ready is None:
        for key, members in classes.items():
            refusal = refused.get(key)
            claims.extend(
                claim_for(
                    rule,
                    outcome,
                    key,
                    run_id=context.run_id,
                    defeaters=(
                        *(
                            held
                            if isinstance(held, tuple)
                            else _defeaters(rule, cast("EntryRecords", held), context)
                        ),
                        *(refusal.on(outcome.subject) if refusal is not None else ()),
                    ),
                )
                for held, outcome in members
            )
    return claims, failures, False


def _propose(
    proposing: Sequence[Rule],
    subjects: Sequence[EntryRecords],
    *,
    report: InferenceReport,
    published: list[Claim],
    edges: Sequence[Assertion],
    constraining: Sequence[Rule],
    predicates: Mapping[str, Predicate],
    shapes: Mapping[str, Reads],
    in_force: Mapping[str, Rule],
    unavailable: Mapping[str, str],
    writers: tuple[FragmentWriter, FragmentWriter],
    journal: Journal | NullJournal,
    context: _Context,
    mentioned: Mapping[str, tuple[Resolution, ...]] | None = None,
    found: list[tuple[str, str, str, str]] | None = None,
) -> None:
    """Run every proposing rule in force within its index, after grouping.

    Pairs are drawn from the classes the grouping phase claimed, read exactly as they were
    published, so a member a defeater retracted is out and an index whose rule may not publish is
    not drawn within. A truncation claim joins the run's claims once, however many proposing rules
    share the index that was cut. Every candidate is written through
    :func:`~ingence.infer.candidates.candidates_to_publish`. Each derivation no constraint refused
    is added to ``found``, for the phase after proposing.
    """
    candidates_writer, claims_writer = writers
    unpublished = {
        tally.rule_id: tally.not_evaluable or tally.not_claim_ready or ""
        for tally in report.groupings
        if tally.not_evaluable or tally.not_claim_ready
    }
    truncations: set[str] = set()
    for rule in _counted(proposing, context.reporter):
        tally = ProposalTally(rule_id=rule.rule_id, rule_version=rule.version)
        report.proposals.append(tally)
        if rule.rule_id in unavailable:
            tally.not_evaluable = unavailable[rule.rule_id]
            continue
        try:
            proposed = propose(
                rule,
                [entry for entry in subjects if applies_at(rule.level, entry.entry)],
                tally=tally,
                published=published,
                edges=edges,
                unpublished=unpublished,
                constraints=constraining,
                predicates=predicates,
                shapes=shapes,
                in_force=in_force,
                run_id=context.run_id,
                token=context.token,
                defeat=functools.partial(_defeaters, rule, context=context),
                mentioned=mentioned,
            )
        except PredicateNotEvaluableError as exc:
            tally.not_evaluable = str(exc)
            journal.record("infer", "rule.not_evaluable", rule_id=rule.rule_id, reason=str(exc))
            continue
        if proposed.cancelled:
            report.cancelled = True
            return
        now = time.monotonic()
        # A pair a person answered is not proposed again on the evidence answered, and every
        # candidate is published through the one function that says so.
        published_pairs, answered_before = candidates_to_publish(
            proposed.candidates,
            context.answers,
            claims_spelling(published) if context.answers is not None else None,
        )
        tally.answered_before = answered_before
        for candidate in published_pairs:
            candidates_writer.add(candidate, now=now)
            if (
                found is not None
                and candidate.primitive is Primitive.DERIVES
                and candidate.decision_tier is not DecisionTier.BLOCKED
                and not rejected_by_a_person(candidate, context.answers)
            ):
                found.append(
                    (candidate.candidate_id, rule.rule_id, candidate.source_id, candidate.target_id)
                )
        for claim in proposed.truncations:
            if claim.claim_id in truncations:
                continue
            truncations.add(claim.claim_id)
            claims_writer.add(claim, now=now)
            published.append(view_of(claim))
            report.claims += 1
        report.candidates += len(published_pairs)
        journal.record(
            "infer",
            "rule.proposed",
            rule_id=rule.rule_id,
            within=tally.within or "",
            pairs=tally.pairs,
            formed=tally.formed,
            supported=tally.supported,
            circumstantial=tally.circumstantial,
            blocked=tally.blocked,
            truncated=tally.truncated,
            answered_before=tally.answered_before,
            not_ready=tally.not_ready or "",
        )


#: The parameter a grouping rule sets to keep the positions of some kinds.
ORDERED_KINDS_PARAMETER = "ordered_component_kinds"


def _keys_on_positions(grouping: Sequence[Rule]) -> bool:
    """Whether any grouping rule in force keys on where a name's components stand."""
    return any(
        bool(parameters_in_force(rule).get(ORDERED_KINDS_PARAMETER))
        for rule in grouping
        if any(specification.name == ORDERED_KINDS_PARAMETER for specification in rule.parameters)
    )


def _flag_reads_slots(flagging: Sequence[Rule]) -> bool:
    """Whether any flagging rule in force reads where a name's components stand.

    A word question is asked of a word standing beside others in one place of sibling names, which
    only the name's layout says. The predicate declares it, and the pass loads the layout for it.
    """
    return any(reads_name_slots(PREDICATES.declaration(rule.predicate)) for rule in flagging)


#: Which region each level a scope rule may declare names. The pass assembles a scope for every
#: one of them, and a rule is handed only the scopes at its own level. A rule about a whole root
#: asked about a root, and handing it eleven thousand folders would answer a question it did not
#: ask once per folder. A rule reading a scope at a level absent from here says so and is not
#: evaluated.
SCOPE_LEVELS: dict[str, ScopeKind] = {"root": ScopeKind.ROOT, "folder": ScopeKind.FOLDER}

#: The level a folder rule declares, which is the one a defeater may read a scope at.
FOLDER_LEVEL = "folder"


def _flag(
    flagging: Sequence[Rule],
    subjects: Sequence[EntryRecords],
    *,
    scopes: Sequence[ScopeRecords] = (),
    report: InferenceReport,
    unavailable: Mapping[str, str],
    writers: tuple[FragmentWriter, FragmentWriter],
    journal: Journal | NullJournal,
    context: _Context,
    handed: Callable[[Rule, Sequence[EntryRecords]], Sequence[EntryRecords]] | None = None,
) -> None:
    """Run every flagging rule in force on every subject at its level, after proposing.

    A finding is not a claim, so a flagging rule raises one whether or not it names a defeater, and
    nothing retracts it. A flag unable to decide on a subject is recorded as a failure against the
    rule, as a classifying rule is.

    A flag reads an entry or a scope. A scope is one indexed root with every entry under it, which
    is what a fact about a region rather than about a file is raised against. That a label recurs
    across folders is nothing any one file carries, and raising it per file would say it once per
    file. A flag reading a group is still not evaluable, because nothing assembles a class of a
    grouping, and it says so.

    Findings are held per rule and written once the rule has read every subject, so a rule that
    turns out not to be evaluable partway through publishes none.
    """
    findings_writer, failures_writer = writers
    for rule in _counted(flagging, context.reporter):
        tally = RuleTally(rule_id=rule.rule_id, rule_version=rule.version)
        report.flags.append(tally)
        shape = context.shapes.get(rule.rule_id)
        reason = unavailable.get(rule.rule_id) or _no_subject_for(rule, shape)
        if reason is not None or shape is None:
            tally.not_evaluable = reason
            journal.record("infer", "rule.not_evaluable", rule_id=rule.rule_id, reason=reason)
            continue
        predicate = context.predicates[rule.rule_id]
        findings: list[Finding] = []
        failures: list[RuleFailure] = []
        try:
            for outcome, kind, about in _flag_outcomes(
                rule,
                predicate,
                subjects if handed is None else handed(rule, subjects),
                scopes,
                reads=shape,
                holders=context.holders,
                beneath=_beneath_a_folder_defeater(rule, context),
            ):
                if context.token is not None and context.token.cancelled:
                    report.cancelled = True
                    return
                tally.count(outcome)
                if outcome.failure_class is FailureClass.INTERPRETATION:
                    failures.append(failure_for(rule, outcome, run_id=context.run_id))
                elif outcome.concluded:
                    # A flag naming defeaters has them evaluated on the entry it raised and
                    # recorded on the finding, which is raised all the same.
                    defeaters = (
                        _defeaters(rule, about, context)
                        if about is not None and rule.defeated_by
                        else ()
                    )
                    findings.append(
                        finding_for(
                            rule,
                            outcome,
                            run_id=context.run_id,
                            subject_kind=kind,
                            defeaters=defeaters,
                        )
                    )
        except PredicateNotEvaluableError as exc:
            report.flags[-1] = RuleTally(
                rule_id=rule.rule_id, rule_version=rule.version, not_evaluable=str(exc)
            )
            journal.record("infer", "rule.not_evaluable", rule_id=rule.rule_id, reason=str(exc))
            continue
        now = time.monotonic()
        for finding in findings:
            findings_writer.add(finding, now=now)
        for failure in failures:
            failures_writer.add(failure, now=now)
        tally.findings = len(findings)
        report.findings += len(findings)
        report.failures += len(failures)
        journal.record(
            "infer",
            "rule.flagged",
            rule_id=rule.rule_id,
            evaluated=tally.evaluated,
            raised=tally.findings,
            no_evidence=tally.no_evidence,
            starved=tally.starved,
            unexamined=tally.unexamined,
            conflicting=tally.conflicting,
        )


def _no_subject_for(rule: Rule, shape: Reads | None) -> str | None:
    """Say why no phase can hand this flagging rule a subject, or None where one can.

    A flag reads one entry's records or a scope. A scope is assembled per indexed root and per
    folder, so a rule reading one at any other level names a region the pass does not build. Saying
    which levels the pass does build separates a rule that is ahead of the pass from a rule that
    asked for the wrong thing.
    """
    if shape is None:
        return "reads nothing, which no phase hands a flagging predicate"
    if shape in ENTRY_SHAPED:
        return None
    if shape is Reads.SCOPE:
        if rule.level in SCOPE_LEVELS:
            return None
        assembled = ", ".join(repr(level) for level in sorted(SCOPE_LEVELS))
        return (
            f"reads a scope at level {rule.level!r}, and the pass assembles a scope at "
            f"{assembled} and at no other level"
        )
    return f"reads a {shape.value}, which no phase hands a flagging predicate"


def _beneath_a_folder_defeater(
    rule: Rule, context: _Context
) -> Callable[[EntryRecords], bool] | None:
    """Return whether an entry lies beneath a folder one of the rule's folder defeaters raised.

    This is what a root-level rule's deferral means. A rule about a whole root has no folder above
    its subject to be defeated at, so a folder defeater it names would never fire. Instead, the
    entries beneath a folder that defeater raised are not part of what the rule reads. A label
    recurring across a program's jars says nothing about a laboratory's replicates. The function
    returns None where the rule names no folder defeater, so nothing is filtered.
    """
    defeaters = [
        context.in_force[name]
        for name in rule.defeated_by
        if name in context.in_force
        and context.shapes.get(name) is Reads.SCOPE
        and context.in_force[name].level == FOLDER_LEVEL
    ]
    if not defeaters:
        return None

    def beneath(records: EntryRecords) -> bool:
        return any(_defeating(defeater, records, context) is not None for defeater in defeaters)

    return beneath


def _flag_outcomes(
    rule: Rule,
    predicate: Predicate,
    subjects: Sequence[EntryRecords],
    scopes: Sequence[ScopeRecords],
    *,
    reads: Reads,
    holders: Mapping[str, EntryRecords] | None = None,
    beneath: Callable[[EntryRecords], bool] | None = None,
) -> Iterator[tuple[Outcome, FindingSubject, EntryRecords | None]]:
    """Yield what one flagging rule came to on each subject its shape and level give it.

    An entry-shaped flag reads every entry at its level. A scope-shaped one reads every scope at
    its level, which is the same restriction one level up: a rule about a root is not asked about a
    folder. Each outcome carries the kind of subject it is about, so a finding is filed by what was
    evaluated and not by what its subject string looks like: an entry identifier and a scope token
    are both strings.

    A folder scope's outcome carries the folder's own records, so a rule about a folder can name a
    defeater about that folder and have it evaluated there. A folder laid out as an installed
    program, or lying beneath one, marks what another rule raised about it. A root scope carries
    none, there being no entry a root is.
    """
    if reads is Reads.SCOPE:
        kind = SCOPE_LEVELS.get(rule.level)
        for scope in scopes:
            if scope.scope.kind is not kind:
                continue
            about = (
                (holders or {}).get(scope.scope.value or "") if kind is ScopeKind.FOLDER else None
            )
            read = scope
            if kind is ScopeKind.ROOT and beneath is not None:
                # A root's rule reads the root without what lies beneath a folder a defeater it
                # names raised: a program's files are not the laboratory's.
                read = replace(
                    scope, entries=tuple(held for held in scope.entries if not beneath(held))
                )
            yield raise_on_scope(rule, predicate, read), FindingSubject.SCOPE, about
        return
    for entry_records in subjects:
        if not applies_at(rule.level, entry_records.entry):
            continue
        yield (
            raise_on_entry(rule, predicate, entry_records, reads=reads),
            FindingSubject.ENTRY,
            entry_records,
        )


def _defeaters(rule: Rule, records: EntryRecords, context: _Context) -> tuple[Defeater, ...]:
    """Evaluate every rule a rule names as its defeater, on the same entry.

    Each is built through the pass's :class:`SharedDefeaters`, so a claim whose defeaters did not
    fire holds the records every such claim of its rule holds.
    """
    return context.shared.of(
        rule,
        tuple(
            context.shared.defeater(
                context.in_force[name], _defeating(context.in_force[name], records, context)
            )
            for name in rule.defeated_by
        ),
    )


def _defeating(defeater: Rule, records: EntryRecords, context: _Context) -> Outcome | None:
    """Return what a defeating rule came to on the same entry, or None where it could not say.

    None is a defeater recorded as what would retract the claim and not fired, which is the honest
    state wherever the defeating rule cannot read this entry.

    A defeater is evaluated the way its own effect is evaluated, so a flag serving as a defeater is
    read as a flag. Read as a classification, a flag that answers with a finding (its records and
    its reason) would reach a reader that refuses one, and the pass would fail on the first entry
    where the flag fired. Reading a flag as a flag costs one branch and removes the trap.
    """
    predicate = context.predicates.get(defeater.rule_id)
    shape = context.shapes.get(defeater.rule_id)
    if predicate is None or shape is None:
        return None
    if shape is Reads.SCOPE and defeater.level == FOLDER_LEVEL:
        return _raised_above(defeater, predicate, records.entry, context)
    if shape not in ENTRY_SHAPED:
        return None
    if not applies_at(defeater.level, records.entry):
        return None
    read = raise_on_entry if defeater.effect is RuleEffect.FLAGS else evaluate_on_entry
    try:
        return read(defeater, predicate, records, reads=shape)
    except PredicateNotEvaluableError:
        return None


def _raised_above(
    defeater: Rule, predicate: Predicate, entry: Entry, context: _Context
) -> Outcome | None:
    """Return what a folder rule raised on the nearest folder holding an entry, or None.

    The rule applies beneath a folder, and not only directly in it. A folder rule is evaluated on a
    folder's direct children, because that is where a layout can be read, and what it raises about
    a folder is about the region the folder holds: an installed program's headers sit three folders
    below the one whose layout says it is a program. So an entry is walked up to its root, the
    entry itself first where it is a folder, and the nearest folder the rule raised is the defeat,
    citing what that folder's finding cites. A folder the rule never raised above an entry leaves
    the defeater recorded unfired.
    """
    verdicts = context.raised.setdefault(defeater.rule_id, {})
    current: str | None = entry.entry_id
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        scope = context.folders.get(current)
        if scope is not None:
            outcome = verdicts.get(current)
            if outcome is None:
                try:
                    outcome = raise_on_scope(defeater, predicate, scope)
                except PredicateNotEvaluableError:
                    return None
                verdicts[current] = outcome
            if outcome.concluded:
                return outcome
        current = context.parents.get(current)
    return None


def _with_miscellaneous_answers(
    records: tuple[EntryRecords, ...], project_path: Path
) -> tuple[EntryRecords, ...]:
    """Mark each folder a person answered about as miscellaneous files."""
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.targets import MISCELLANEOUS_PREFIX

    prefix = f"{MISCELLANEOUS_PREFIX}:"
    answers = {
        target[len(prefix) :]: str(event.kind.value)
        for target, event in latest_by_target(read_events(project_path)).items()
        if target.startswith(prefix)
        and event.kind in (RecordEventKind.CONFIRM, RecordEventKind.REJECT)
    }
    if not answers:
        return records
    return tuple(
        replace(
            held, contents=replace(held.contents, miscellaneous_answer=answers[held.entry.entry_id])
        )
        if held.contents is not None and held.entry.entry_id in answers
        else held
        for held in records
    )


def _with_folders_not_programs(
    records: tuple[EntryRecords, ...], project_path: Path
) -> tuple[EntryRecords, ...]:
    """Mark each folder the Record says is not a program, or is one, and no other."""
    from ingence.infer.deferral import folders_said_programs
    from ingence.record.log import read_events
    from ingence.record.verdicts import folders_not_programs

    events = read_events(project_path)
    named = folders_not_programs(events)
    # And each folder a person said is a program: the answer to a question about software outside
    # the catalogue puts it in program scope.
    confirmed = folders_said_programs(events)
    if not named and not confirmed:
        return records
    return tuple(
        replace(
            held,
            not_a_program=held.entry.entry_id in named,
            a_program=held.entry.entry_id in confirmed,
        )
        if held.entry.entry_id in named or held.entry.entry_id in confirmed
        else held
        for held in records
    )


def _placed_in_experiments(
    subjects: Sequence[EntryRecords],
    rules: Sequence[Rule],
    in_force: Mapping[str, Rule],
    *,
    resolving: bool,
    shapes: Mapping[str, Reads],
) -> tuple[tuple[EntryRecords, ...], dict[str, str]]:
    """Where each file lies in its experiment, and each file's experiment folder.

    The places are handed to the subjects where a grouping rule compares within an experiment. The
    plain mapping is kept where mentions will be resolved, since a script's bare file names are
    looked for in its experiment. Neither is computed where nothing reads it.
    """
    from ingence.infer.experiment_places import (
        acquisitions_named,
        compares_within_an_experiment,
        experiment_places,
        one_name_beside,
        series_summarised,
        with_acquisitions_named,
        with_experiment_places,
        with_named_beside,
    )

    comparing = compares_within_an_experiment(rules)
    naming = [rule for rule in rules if shapes.get(rule.rule_id) is Reads.NAMED_ACQUISITION]
    if not comparing and not naming and not resolving:
        return tuple(subjects), {}
    places = experiment_places(subjects, in_force)
    experiment_of = {entry_id: place.experiment for entry_id, place in places.items()}
    placed = tuple(subjects)
    if comparing:
        placed = with_experiment_places(placed, places)
    if naming:
        # The smallest minimum any rule reading the shape names, on ``_minimum_key_characters``'
        # precedent: no rule is handed fewer matches than it asked for, and each reads its own
        # minimum from what it is handed.
        declared = [
            int(str(parameters_in_force(rule)["minimum_key_characters"]))
            for rule in naming
            if any(spec.name == "minimum_key_characters" for spec in rule.parameters)
        ]
        named = acquisitions_named(
            placed,
            places,
            **({"minimum_key_characters": min(declared)} if declared else {}),
            annotation_senses=_declared_strings(naming, "annotation_senses"),
            image_formats=_declared_strings(naming, "image_formats"),
            not_made_from_senses=_declared_strings(naming, "not_made_from_senses"),
            levels_up=_declared_most(naming, "whole_name_levels_up"),
        )
        placed = with_acquisitions_named(placed, named)
        # A series and the table named for it, and one name in sibling folders, where a rule
        # reading the shape declares what they are read by.
        series = series_summarised(
            placed,
            summary_senses=_declared_strings(naming, "summary_senses"),
            table_formats=_declared_strings(naming, "table_formats"),
            minimum_series_members=_declared_least(naming, "minimum_series_members"),
        )
        beside = one_name_beside(
            placed,
            places,
            source_senses=_declared_strings(naming, "source_folder_senses"),
            product_senses=_declared_strings(naming, "product_folder_senses"),
            maximum_folders=_declared_most(naming, "maximum_folders"),
            ignored_roles=_declared_strings(naming, "ignored_roles"),
            apart_senses=_declared_strings(naming, "apart_folder_senses"),
        )
        placed = with_named_beside(placed, series, beside)
    return placed, experiment_of


def _declared_strings(rules: Sequence[Rule], name: str) -> frozenset[str]:
    """Every value the rules declaring a string-list parameter give it: what the named-for pass
    reads an annotation by, so no rule is handed fewer matches than it asked for."""
    found: set[str] = set()
    for rule in rules:
        if any(spec.name == name for spec in rule.parameters):
            declared = parameters_in_force(rule)[name]
            if isinstance(declared, (list, tuple)):
                found.update(str(value) for value in declared)
    return frozenset(found)


def _declared_least(rules: Sequence[Rule], name: str) -> int:
    """The smallest value the rules declaring an integer parameter give it, or 0 where none
    declares it: no rule is handed fewer matches than it asked for."""
    found = [
        int(str(parameters_in_force(rule)[name]))
        for rule in rules
        if any(spec.name == name for spec in rule.parameters)
    ]
    return min(found) if found else 0


def _declared_most(rules: Sequence[Rule], name: str) -> int:
    """The largest value the rules declaring an integer parameter give it, or 0 where none does."""
    found = [
        int(str(parameters_in_force(rule)[name]))
        for rule in rules
        if any(spec.name == name for spec in rule.parameters)
    ]
    return max(found) if found else 0


def _with_approved_groups(
    subjects: Sequence[EntryRecords],
    project_path: Path,
    in_force: Mapping[str, Rule],
    rules: Sequence[Rule],
    shapes: Mapping[str, Reads],
) -> tuple[EntryRecords, ...]:
    """Hand each entry the approved group of experiments whose member folder lies above it.

    The approval is read from the Record and never guessed. A group counts where the latest
    decision on its question (``question:<proposal rule>:<group>``, an earlier answer) confirms it.
    It also counts where the latest decision on its core (``group:<terms>``, where a page writes a
    proposal's answer) confirms it and the proposal was put to the person: the answer names the
    proposal's question, or the run it was made against raised it. A set of words approved from a
    question the lattice asked approves no proposal nobody was shown. The proposal is recomputed
    here from the same records and the proposal rule's parameters, by the one function its flag
    raises from, so an approval names what the page showed. With no approval in the Record nothing
    is computed.
    """
    import json

    from ingence.grouping.asked_again import AskedAgain, ProposalMembers
    from ingence.grouping.resolutions import spelled
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.questions import question_target
    from ingence.record.targets import group_terms_named_by
    from ingence.rulebook.predicates.experiments import AnswerReader, proposed_group_cores
    from ingence.storage.catalog import open_catalog

    reader = next(rule for rule in rules if shapes.get(rule.rule_id) is Reads.APPROVED_GROUP)
    proposal_rule = str(parameters_in_force(reader).get("proposal_rule", ""))
    if proposal_rule not in in_force:
        return tuple(subjects)
    prefix = question_target(proposal_rule, "")
    latest = latest_by_target(read_events(project_path)).items()
    reading = AnswerReader(subjects)
    # A question answered before the lattice's words changed is still answered. The proposal a
    # person confirmed on ``kelt vorn [...]`` is the one now worded ``kelt_line vorn [...]``. The
    # function finds it by its members (the experiments it names) in the run the answer was made
    # against, and asks again where they are not the same.
    members_then = ProposalMembers(project_path)

    def members_of(target: str, event: RecordEvent) -> frozenset[str] | None:
        return members_then.of(str(event.against_run_id), target)

    approved = AskedAgain(
        reading.words,
        {
            target: event
            for target, event in latest
            if target.startswith(prefix) and event.kind is RecordEventKind.CONFIRM
        },
        rank=lambda event: event.sequence,
        members_then=members_of,
    )
    # A page answers a proposal on its core: a confirmed core approves every proposal offering it.
    # The core is read in the words the lattice holds (``groups.words_as``): an approval given on
    # spellings finds its core when the lattice reads terms, and the reverse.
    approved_cores = {
        spelled(words): event
        for target, event in latest
        if event.kind is RecordEventKind.CONFIRM
        and (terms := group_terms_named_by(target)) is not None
        for words in reading(terms)
    }
    if not approved and not approved_cores:
        return tuple(subjects)
    raised_in: dict[str, frozenset[str]] = {}

    def raised_by(run_id: str) -> frozenset[str]:
        """What the proposal rule raised in one run, read from that run's own findings."""
        if run_id not in raised_in:
            try:
                with open_catalog(project_path, run_id=run_id) as catalog:
                    rows = catalog.query(
                        "SELECT DISTINCT u.value FROM findings f, unnest(f.raised) AS u(value) "
                        "WHERE f.rule_id = ?",
                        [proposal_rule],
                    ).fetchall()
                raised_in[run_id] = frozenset(str(value) for (value,) in rows)
            except Exception:  # a run no longer readable showed nothing to approve
                raised_in[run_id] = frozenset()
        return raised_in[run_id]

    raised_then: dict[str, AskedAgain[bool]] = {}

    def raised_in_run(run_id: str, value: str, these: frozenset[str]) -> bool:
        """Whether the run raised this proposal, worded as it was then or as it is now (the same
        members)."""
        if value in raised_by(run_id):
            # Worded as it was, but the same group only where its members are (where that run can
            # still say them).
            then = members_then.of(run_id, prefix + value)
            return then is None or then == these
        if run_id not in raised_then:
            raised_then[run_id] = AskedAgain(
                reading.words,
                {prefix + found: True for found in raised_by(run_id)},
                members_then=lambda target, _: members_then.of(run_id, target),
            )
        return raised_then[run_id].find(prefix + value, members=these) is not None

    def put_to_them(value: str, core: str, these: frozenset[str]) -> bool:
        event = approved_cores.get(core)
        if event is None:
            return False
        try:
            said = json.loads(event.payload or "{}")
        except (TypeError, ValueError):
            said = {}
        named = said.get("question") if isinstance(said, dict) else None
        if isinstance(named, str):
            asked = AskedAgain(reading.words, {named: event}, members_then=members_of).find(
                prefix + value, members=these
            )
            if asked is not None:
                return True
        return bool(event.against_run_id) and raised_in_run(str(event.against_run_id), value, these)

    parameters = dict(parameters_in_force(in_force[proposal_rule]))
    members: dict[str, str] = {}
    for value, group, core in proposed_group_cores(subjects, parameters):
        these = frozenset(group.members)
        confirmed = approved.find(prefix + value, members=these) is not None
        if confirmed or (core and put_to_them(value, spelled(core), these)):
            for member in group.members:
                members[member] = value

    def group_of(path: str) -> str | None:
        cursor = path
        while cursor:
            if cursor in members:
                return members[cursor]
            cursor = cursor.rpartition("/")[0]
        return None

    # A part recovered from inside a file is not a member. It shares its container's path, and the
    # container is the experiment's file. The part is evidence about it.
    return tuple(
        replace(
            held,
            approved_group=None
            if held.entry.entry_kind is EntryKind.DERIVED
            else group_of(held.entry.relative_path),
        )
        for held in subjects
    )


def _group_resolutions(
    subjects: Sequence[EntryRecords],
    project_path: Path,
    in_force: Mapping[str, Rule],
    journal: Journal | NullJournal | None = None,
) -> dict[str, GroupResolution]:
    """What the core sets of terms a person approved resolve, by entry.

    The decision is read from the Record, from any actor alike. A core counts where the latest
    decision on its target (``group:<terms>``) confirms it, and is declined where it rejects it.
    The resolution is computed only where a rule in force reads one. Under
    ``derivations: each_deviation``, it is computed only where the Record holds an approved core.
    Under ``lattice`` the most general sets are asked before any approval, so it is computed
    always. The parameters are the first such rule's (by identifier) that declares them, and the
    experiments are its ``proposal_rule``'s. Where the lattice stopped at its bound, the
    ``journal`` says so.
    """
    from ingence.grouping.resolutions import EACH_DEVIATION
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.targets import group_terms_named_by
    from ingence.rulebook.predicates.experiments import (
        RESOLUTION_DEFAULTS,
        RESOLUTION_PREDICATES,
        AnswerReader,
        derivation_mode,
        group_resolutions,
        resolution_parameters,
    )

    readers = [
        rule for name, rule in sorted(in_force.items()) if rule.predicate in RESOLUTION_PREDICATES
    ]
    if not readers:
        return {}
    decided = sorted(latest_by_target(read_events(project_path)).items())
    # Each stored answer in the words the lattice holds now (``groups.words_as``).
    reading = AnswerReader(subjects)
    cores = [
        words
        for target, event in decided
        if event.kind is RecordEventKind.CONFIRM
        and (terms := group_terms_named_by(target)) is not None
        for words in reading(terms)
    ]
    declined = [
        words
        for target, event in decided
        if event.kind is RecordEventKind.REJECT
        and (terms := group_terms_named_by(target)) is not None
        for words in reading(terms)
    ]
    declaring = next(
        (rule for rule in readers if any(s.name == "term_source" for s in rule.parameters)), None
    )
    declared = dict(parameters_in_force(declaring)) if declaring is not None else {}
    named = str({**RESOLUTION_DEFAULTS, **declared}.get("proposal_rule", ""))
    proposal = in_force.get(named)
    parameters = resolution_parameters(
        declared, dict(parameters_in_force(proposal)) if proposal is not None else None
    )
    try:
        if not cores and derivation_mode(parameters) == EACH_DEVIATION:
            return {}
        found = group_resolutions(subjects, cores, parameters, declined=declined)
    except PredicateNotEvaluableError:
        return {}
    # Under the bound the lattice asks nothing, and the run says so.
    bound = int(getattr(found, "lattice_bound", 0) or 0)
    if bound and journal is not None:
        journal.record("infer", "groups.lattice_bound_reached", maximum_concepts=bound)
    return found


def _declined_groups(
    project_path: Path, in_force: Mapping[str, Rule], subjects: Sequence[EntryRecords] = ()
) -> frozenset[str]:
    """The core sets of terms a person declined as groups, spelled canonically.

    Each is in the words the lattice holds now (``groups.words_as``).
    """
    from ingence.grouping.resolutions import spelled
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.targets import group_terms_named_by
    from ingence.rulebook.predicates.experiments import RESOLUTION_PREDICATES, AnswerReader

    if not any(rule.predicate in RESOLUTION_PREDICATES for rule in in_force.values()):
        return frozenset()
    reading = AnswerReader(subjects)
    return frozenset(
        spelled(words)
        for target, event in latest_by_target(read_events(project_path)).items()
        if event.kind is RecordEventKind.REJECT
        and (terms := group_terms_named_by(target)) is not None
        for words in reading(terms)
    )


def _with_resolutions(
    records: Sequence[EntryRecords], resolved: Mapping[str, GroupResolution]
) -> tuple[EntryRecords, ...]:
    """Hand each entry what the approved cores resolved for it, and nothing else."""
    return tuple(
        replace(held, group_resolution=resolved[held.entry.entry_id])
        if held.entry.entry_id in resolved
        else held
        for held in records
    )


def _minimum_key_characters(in_force: Mapping[str, Rule], shapes: Mapping[str, Reads]) -> int:
    """The shortest acquisition name matched on, as the rules reading folder contents declare it.

    Where several rules read the shape and name different values, the smallest is counted with, so
    no rule is handed fewer matches than it asked for; the rules shipped declare one value.
    """
    declared = [
        int(str(parameters_in_force(rule)["minimum_key_characters"]))
        for name, rule in sorted(in_force.items())
        if shapes.get(name) is Reads.FOLDER_CONTENTS
        and any(spec.name == "minimum_key_characters" for spec in rule.parameters)
    ]
    return min(declared, default=MINIMUM_KEY_CHARACTERS)


def _minimum_items(
    in_force: Mapping[str, Rule], shapes: Mapping[str, Reads], parameter: str = "minimum_items"
) -> int:
    """The fewest per-item subfolders making one run's outputs: of one template
    (``minimum_items``) or holding the same files (``minimum_alike_items``); and the most folders
    on one date a series of days holds (``most_folders_a_date``).

    The rules reading folder contents declare these, and the smallest is taken where several differ.
    """
    declared = [
        int(str(parameters_in_force(rule)[parameter]))
        for name, rule in sorted(in_force.items())
        if shapes.get(name) is Reads.FOLDER_CONTENTS
        and any(spec.name == parameter for spec in rule.parameters)
    ]
    defaults = {
        "minimum_items": MINIMUM_ITEMS,
        "minimum_alike_items": MINIMUM_ALIKE_ITEMS,
        "most_folders_a_date": MOST_FOLDERS_A_DATE,
    }
    return min(declared, default=defaults[parameter])


def _with_neighbours(records: Sequence[EntryRecords]) -> tuple[EntryRecords, ...]:
    """Attach to each file what its neighbours hold: the sibling keys of files holding bytes.

    The work is linear in the pool, and a folder costs one pair of frozen sets. ``beside`` is the
    file's own folder. ``nearby`` is every folder beside it under the same parent, computed once
    per parent and not once per folder, so a folder with a thousand sibling folders costs a
    thousand unions and not a million.
    """
    holding: dict[str | None, set[str]] = {}
    batches: dict[str | None, set[str]] = {}
    naming: dict[str | None, set[str]] = {}
    children: dict[str | None, list[str]] = {}
    parent_of: dict[str, str | None] = {}
    for held in records:
        entry = held.entry
        if entry.entry_type is EntryType.DIRECTORY:
            parent_of[entry.entry_id] = entry.parent_entry_id
            children.setdefault(entry.parent_entry_id, []).append(entry.entry_id)
        elif entry.entry_type is EntryType.FILE and (entry.size_bytes or 0) > 0:
            holding.setdefault(entry.parent_entry_id, set()).add(sibling_key(entry.name))
            naming.setdefault(entry.parent_entry_id, set()).add(entry.name.casefold())
            template = template_key(entry.name)
            if template:
                batches.setdefault(entry.parent_entry_id, set()).add(template)
    beside = {folder: frozenset(keys) for folder, keys in holding.items()}
    names = {folder: frozenset(keys) for folder, keys in naming.items()}
    templates = {folder: frozenset(keys) for folder, keys in batches.items()}
    empty: frozenset[str] = frozenset()
    nearby = {
        parent: frozenset().union(*(beside.get(child, empty) for child in folders))
        for parent, folders in children.items()
    }
    return tuple(
        replace(
            held,
            beside=beside.get(held.entry.parent_entry_id, empty),
            nearby=nearby.get(parent_of.get(held.entry.parent_entry_id or ""), empty),
            templates=templates.get(held.entry.parent_entry_id, empty),
            beside_names=names.get(held.entry.parent_entry_id, empty),
        )
        if held.entry.entry_type is not EntryType.DIRECTORY
        else held
        for held in records
    )


def _suggest_workflows(
    project: Project,
    records: Sequence[EntryRecords],
    published: Sequence[Claim],
    declared: Sequence[Workflow],
    experiment_of: Mapping[str, str],
    in_force: Mapping[str, Rule],
    staging: Path,
    journal: Journal | NullJournal,
) -> tuple[tuple[Workflow, ...], dict[str, str]]:
    """The workflows the run applies, each file's experiment as the suggester read it, and the
    run's statement of the workflows in its own directory.

    The suggester reads the results of the earlier phases (the claims published so far, each file's
    experiment). A suggestion is in force as assumed unless a declared workflow of its identifier
    replaces it or a refusal in the Record (read at the run's watermark, as every decision is) names
    it. With ``infer.workflows`` off nothing is suggested and the declared workflows alone stand.
    The run says so in the same file, so the workflows route reads one shape either way.
    """
    from ingence.infer.workflow_suggest import (
        suggest_workflows,
        workflows_in_force_for,
        write_workflows,
    )
    from ingence.record.workflow import Refusals, workflows_refused

    suggested: tuple[Workflow, ...] = ()
    refused: Refusals = Refusals()
    placed = dict(experiment_of)
    if project.settings.infer.workflows:
        from ingence.infer.experiment_places import experiment_places

        with_claims = _with_claims(records, published, declared)
        if not placed:
            placed = {
                entry_id: place.experiment
                for entry_id, place in experiment_places(with_claims, in_force).items()
            }
        suggested = suggest_workflows(
            with_claims,
            placed,
            minimum_experiments=project.settings.infer.workflow_minimum_experiments,
        )
        refused = workflows_refused(project.path)
    applied = workflows_in_force_for(declared, suggested, refused)
    write_workflows(staging, declared=declared, suggested=suggested, refused=refused)
    journal.record(
        "infer",
        "workflows.read",
        declared=[workflow.identifier for workflow in declared],
        suggested=[workflow.identifier for workflow in suggested],
        in_force=[workflow.identifier for workflow in applied],
    )
    return applied, placed


def _with_claims(
    records: Sequence[EntryRecords],
    claims: Sequence[Claim],
    workflows: Sequence[Workflow] = (),
) -> tuple[EntryRecords, ...]:
    """Attach the claims published so far to the entries they are about, in canonical order.

    The workflows in force travel with them, on ``spellings``' precedent: the same tuple for every
    entry, so a rule reading a stage claim can say whether it places the file past the rung where
    data enters. The stage word alone cannot say so.
    """
    about: dict[str, list[Claim]] = {}
    for claim in sorted(claims, key=lambda claim: claim.claim_id):
        about.setdefault(claim.subject, []).append(claim)
    declared = tuple(workflows)
    return tuple(
        replace(
            entry_records,
            claims=tuple(about.get(entry_records.entry.entry_id, ())),
            workflows=declared,
        )
        for entry_records in records
    )


def _with_derivations(
    records: Sequence[EntryRecords],
    derivations: Sequence[tuple[str, str, str, str]],
    claims: Sequence[Claim],
) -> tuple[EntryRecords, ...]:
    """Hand each file the derivations proposed into it, each with its source's stages.

    A source's stages are its resolved classifications, each with its claim, which the reader
    narrows to the stages it names. Only the files a derivation reaches are changed.
    """
    into: dict[str, list[tuple[str, str, str, str]]] = {}
    for derivation in derivations:
        into.setdefault(derivation[3], []).append(derivation)
    sources = {source for _, _, source, _ in derivations}
    held: dict[str, list[tuple[str, str]]] = {}
    for claim in claims:
        if (
            claim.subject in sources
            and claim.predicate.startswith(f"{RuleEffect.CLASSIFIES.value}.")
            and claim.resolution_status is ResolutionStatus.RESOLVED
            and claim.object_reference is not None
        ):
            held.setdefault(claim.subject, []).append((claim.object_reference, claim.claim_id))
    entries = {
        entry_records.entry.entry_id: entry_records.entry
        for entry_records in records
        if entry_records.entry.entry_id in sources
    }
    return tuple(
        replace(
            entry_records,
            derived_from=tuple(
                Derivation(
                    candidate_id=candidate_id,
                    rule_id=rule_id,
                    source_id=source_id,
                    source_name=entries[source_id].name if source_id in entries else source_id,
                    source_stages=tuple(sorted(held.get(source_id, ()))),
                )
                for candidate_id, rule_id, source_id, _ in sorted(
                    into[entry_records.entry.entry_id]
                )
            ),
        )
        if entry_records.entry.entry_id in into
        else entry_records
        for entry_records in records
    )


def _record_identifiers(records: EntryRecords) -> Iterable[str]:
    """Every identifier a claim about this entry could cite as an input."""
    yield records.entry.entry_id
    yield from (component.component_id for component in records.components)
    yield from (binding.binding_id for binding in records.bindings)
    yield from (observation.observation_id for observation in records.observations)


def _rows(result: object) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = result.to_arrow_table().to_pylist()  # type: ignore[attr-defined]
    return rows


def _predicate_snapshot() -> tuple[ComponentActivation, ...]:
    """Record which predicates were registered, enabled and able to run.

    A run listing only what fired cannot be compared with a run under a different configuration.
    """
    for name in PREDICATES.names():
        PREDICATES.probe(name)
    return tuple(ComponentActivation(**row) for row in PREDICATES.snapshot())


def _proposal_ends(
    proposing: Sequence[Rule],
    published: Sequence[Claim],
    mentioned: Mapping[str, tuple[Resolution, ...]] | None,
) -> frozenset[str] | None:
    """The entries a proposing rule in force can draw into a pair, or None for every entry.

    These are each member of a class of the grouping rule an index names, every end of a resolved
    mention for the two mention indexes, and every entry where a rule draws within a folder.
    """
    wanted: set[str] = set()
    for rule in proposing:
        within = rule.candidates_within
        if within is None:
            continue
        if within == PARENT_INDEX:
            return None
        if within in (MENTION_INDEX, CO_MENTION_INDEX):
            for source, resolutions in (mentioned or {}).items():
                wanted.add(source)
                wanted.update(held.target_id for held in resolutions if held.target_id)
            continue
        wanted.update(claim.subject for claim in published if claim.rule_id == within)
    return frozenset(wanted)


def _observed(
    subjects: Sequence[EntryRecords],
    wanted: frozenset[str] | None,
    load: Callable[[Sequence[str]], Mapping[str, Sequence[Observation]]],
    batch: int,
) -> tuple[EntryRecords, ...]:
    """Hand the wanted entries (every entry where None) their observations, loaded by batch."""
    positions = [
        position
        for position, held in enumerate(subjects)
        if wanted is None or held.entry.entry_id in wanted
    ]
    found: dict[str, Sequence[Observation]] = {}
    for start in range(0, len(positions), max(1, batch)):
        found.update(
            load(
                [subjects[position].entry.entry_id for position in positions[start : start + batch]]
            )
        )
    return tuple(
        replace(held, observations=tuple(found[held.entry.entry_id]))
        if held.entry.entry_id in found
        else held
        for held in subjects
    )


def _carried(catalog: Catalog, writer: FragmentWriter) -> list[Claim]:
    """Carry the scan's claims forward: each written as it is read, by batch, and held as its view.

    Only the scan's claims are read, because they are the only ones carried. What the pass holds of
    a claim once it is written is its view: every later phase and the invariant check read it
    exactly as they read the record.
    """
    held: list[Claim] = []
    for row in batched_rows(
        catalog,
        "SELECT * FROM claims WHERE "  # noqa: S608 - a placeholder per constant prefix
        + " OR ".join("starts_with(rule_id, ?)" for _ in SCAN_RULE_PREFIXES)
        + " ORDER BY claim_id",
        list(SCAN_RULE_PREFIXES),
    ):
        if str(row["rule_id"]).startswith(SCAN_RULE_PREFIXES):
            claim = record_from_row(row, Claim)
            writer.add(claim, now=time.monotonic())
            held.append(view_of(claim))
    return held


def _without_observations(records: Sequence[EntryRecords]) -> tuple[EntryRecords, ...]:
    """The records a region is assembled from: each entry's own, but for its observations.

    The records are the same whole or streamed. A streamed pass never holds the pool's
    observations, so the scopes of both passes hold none, and a rule reading a region reads the
    same records either way. An entry with no observations is handed on as it is.
    """
    return tuple(replace(held, observations=()) if held.observations else held for held in records)
