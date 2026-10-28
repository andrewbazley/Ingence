"""What a rule predicate is handed, decided by the rule's effect and declared by the predicate.

Every predicate's registration says which part of a subject it reads, beside the effect it serves
and the evidence families it may contribute. The pass hands it exactly that and nothing more. The
rule's effect decides which subjects exist at all:

- A classifying or grouping rule is handed one entry's records, narrowed to what its predicate
  reads: the entry itself, its components, its bindings, its observations, all of them together,
  the entry beside its root, or the entry beside the Dictionary's spellings.
- A proposing or constraining rule is handed a pair, drawn from one class of a declared index and
  never from the whole pool.
- A flagging rule is handed one entry's records, a group, or a scope. A scope is one region of the
  pool with every entry it holds, which is what a fact about a region rather than about a file is
  raised against.
- The scan layer's own rules are handed what enumeration found, a directory's listing or its
  counts, an object's mode or a member recovered from a container. The census and the cascade hand
  these over, and the pass never does.

The subjects are narrowed so that five of the oldest predicates keep their code. Each of them
narrows an untyped subject on arrival: three take a sequence of bindings, one a sequence of
components, one an entry. Declaring those as what they read, and handing them exactly that, keeps
their code, versions and tests unchanged. This matters because they are the only predicates with
coverage against real record types.

A predicate never reaches the project. A predicate that could reach the project could write to it,
and a narrow subject is what makes every predicate testable from records built by hand.

The claims earlier phases made are part of an entry's records. A proposing rule reading the stage
of each end of a pair is reading a classification, which is why the phases run in the order their
effects give them: classify, group, propose, constrain, flag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.registry import Reads

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from ingence.core.ids import Scope
    from ingence.core.schema import (
        Claim,
        Component,
        DictionaryBinding,
        Entry,
        Observation,
        Root,
        ScanError,
    )
    from ingence.dictionary.programs import ProgramLayout
    from ingence.infer.references import Resolution
    from ingence.record.workflow import Workflow
    from ingence.rulebook.registry import DirectoryTotals

#: A run of digits, which siblings' names differ in and nothing else.
_DIGITS = re.compile(r"\d+")

#: What cuts a name into the parts a batch template is read from.
_TEMPLATE_SEPARATORS = re.compile(r"[^0-9a-z]+")

__all__ = [
    "AnsweredCopies",
    "ApprovedGroup",
    "ArchivedCopy",
    "CandidateSet",
    "Derivation",
    "DerivedFrom",
    "DirectoryCounts",
    "EntryAmongSiblings",
    "EntryInRoot",
    "EntryObservations",
    "EntryRecords",
    "ExperimentPlace",
    "FolderContents",
    "GroupResolution",
    "Listing",
    "NameDates",
    "NameSpellings",
    "NamedAcquisition",
    "NamedFor",
    "PairRecords",
    "ScopeRecords",
    "name_dates",
    "sibling_key",
    "template_key",
]


@dataclass(frozen=True, slots=True)
class Listing:
    """One directory and the entries enumeration listed directly inside it.

    This is what the census hands the rule that draws containment: the listing as found, so a
    directory's edges come from one verdict and not from a constant beside each edge.
    """

    directory: str
    entries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DirectoryCounts:
    """What enumeration counted directly inside one directory, with its subdirectories' totals.

    This is what the census hands the rule behind its closed-world counts, deepest directory first,
    so every subdirectory's totals exist before its parent's are asked for.
    """

    files: int
    directories: int
    size_bytes: int
    children: tuple[DirectoryTotals, ...] = ()


@dataclass(frozen=True, slots=True)
class EntryInRoot:
    """One entry beside the record of the root it was found under.

    What a rule comparing observed filesystem facts is handed: the facts, and whether
    the root's filesystem ignores case, which decides whether two names differing only
    in case are one name. Never the entry's name records, so the pass's guards about
    names never read into a rule that compares no name token.
    """

    entry: Entry
    #: The root's record, or None where the run holds none for it.
    root: Root | None = None


@dataclass(frozen=True, slots=True)
class NameSpellings:
    """One entry beside every spelling the Dictionary gives each sense.

    This is what a rule reading a whole name, or the start or end of one, is handed. The census
    cuts a name into components and binds those, and keeps neither a whole name nor an affix as a
    component, so a sense spelled ``.DS_Store``, ``._`` or ``.part`` binds nothing. Handing the
    spellings over lets the predicate read the name against them without reaching the Dictionary.
    """

    entry: Entry
    #: Every spelling of each sense, by the sense's term.
    spellings: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class FormatOrigins:
    """One entry's name records beside the origin declared for each format spelling.

    This is what a rule reading where a file can have come from is handed. The type says what a
    file holds, and the origin says who can have written it: ``nd2`` and ``tif`` are both images,
    and only one of them is written by an instrument as it acquires. The origin belongs to a
    spelling and not to the type, so the predicate matches a binding's own component against the
    Dictionary's declaration and never reaches the Dictionary to do it.
    """

    entry: Entry
    components: tuple[Component, ...]
    bindings: tuple[DictionaryBinding, ...]
    #: By sense term, each declared spelling (casefolded) and who writes it. A sense that declares
    #: none is absent. A sense that declares an empty map is present and empty.
    origins: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class EntryAmongSiblings:
    """One entry beside what its neighbours hold.

    This is what a rule reading proximity is handed. Replicates of the same experiment in the same
    folder or nearby show that an empty tracking file is a real file with no content. So the pass
    hands over, for the entry's own folder and for the folders beside it, the names of the files
    holding bytes, but for their numbers and their extension (:func:`sibling_key`). A replicate's
    tracks and its neighbour's differ only in a number, and a tool writing one movie's tracks twice,
    as text and as a table, writes one name under two extensions. The pass hands over keys and never
    entries, so a predicate cannot reach a neighbour's records and a finding still rests on the
    entry alone.
    """

    entry: Entry
    #: The keys of the files holding bytes in the entry's own folder.
    beside: frozenset[str] = frozenset()
    #: The keys of the files holding bytes in the folders beside the entry's folder.
    nearby: frozenset[str] = frozenset()
    #: The batch templates of the files holding bytes in the entry's own folder:
    #: :func:`template_key`, what one tool's batch writes for every movie it ran over.
    templates: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class FolderContents:
    """One folder beside what it holds, counted from names and declared formats.

    This is what a rule reading where an experiment's material is bound together is handed
    (:mod:`ingence.infer.folder_contents` counts it). The fields are counts and never another
    folder's records: how many files here and beneath are an instrument's acquisitions, how many
    are named for one, how much of that the files of the folders above it share, how much its
    parent holds directly, and how its subfolders holding such material are linked to one another
    by the acquisition names they share, with the same three figures for its parent's subfolders.
    Two more facts are about names as the tokeniser read them: whether this folder's name holds a
    date and a word, and how many folders above it do. The last is the copy's mark on its own
    files: how many were born after they were last modified, and when those births began and ended.
    """

    entry: Entry
    #: Acquisitions (an instrument's spellings, archive links included) directly inside.
    acquisitions_here: int = 0
    #: Other files directly inside whose names hold an acquisition's name whole.
    named_here: int = 0
    #: The same two, in this folder and every folder beneath it.
    acquisitions_beneath: int = 0
    named_beneath: int = 0
    #: Of the files named for an acquisition beneath, those whose acquisition also lies beneath.
    named_beneath_own: int = 0
    #: Acquisition names held beneath this folder that the own files of a folder above it (its
    #: parent, or any folder above that) hold or are named for.
    shared_above: int = 0
    #: Subfolders holding acquisitions or files named for them; the most of those linked to one
    #: another by an acquisition name they share; and how many hold both kinds.
    holding_children: int = 0
    linked_children: int = 0
    whole_children: int = 0
    #: Acquisitions and files named for one directly inside the parent.
    parent_material_here: int = 0
    #: The same three figures for the parent's subfolders, this folder among them.
    parent_holding_children: int = 0
    parent_linked_children: int = 0
    parent_whole_children: int = 0
    #: One run's outputs, one subfolder per item. This counts how many of this folder's subfolders
    #: each hold only files named for one acquisition, written from one template with the
    #: acquisition's name standing in it (``Traj_<movie>_tracks.csv`` beside
    #: ``Plot_<movie>_tracks.png``). It counts them where at least the declared minimum share one
    #: template, else 0. It also records whether this folder is one of its parent's such
    #: subfolders.
    item_children: int = 0
    one_item_of_many: bool = False
    #: Or holding the same files: a subfolder holding files and no subfolder, whose files read alike
    #: in every item's folder (``tracks.csv`` and ``params.txt``, or ``<item>_tracks.csv``) and
    #: whose name has the same words as its siblings'. It is counted into ``item_children`` where
    #: at least that kind's declared minimum share one shape. Then how many of this folder's
    #: subfolders hold such items, and how many item folders lie beneath it.
    item_holding_children: int = 0
    items_beneath: int = 0
    #: Whether this item's siblings among the run's items share its name's words, as many as its
    #: kind asks. Only then is its dated name not an experiment's.
    one_item_of_a_family: bool = False
    #: The parent's own contents, so a run's output folder can ask whether the folder holding it is
    #: itself bound further up. It is None at a data pool's root.
    parent: FolderContents | None = None
    #: A person's answer on whether this folder is their miscellaneous files, read from the Record:
    #: ``confirm``, ``reject``, or None where nobody has answered.
    miscellaneous_answer: str | None = None
    #: Whether the folder's own name holds a date and a word, as the tokeniser read it.
    name_dated: bool = False
    #: How many folders above this one have such a name.
    dated_ancestors: int = 0
    #: Files directly inside with both a birth and a modification recorded; how many of them were
    #: born after they were last modified (a copy's mark); and the first and last of those births.
    dated_files_here: int = 0
    born_late_here: int = 0
    late_births_first_ns: int | None = None
    late_births_last_ns: int | None = None
    #: Files directly inside, and those of them whose listing declared a copy's arrival (an
    #: archive's write, an upload), with the first and last of those times. A declared copy time is
    #: never a file's own time. It says when a copy landed, which is why it is counted here and
    #: read by no rule that orders files.
    files_here: int = 0
    declared_copies_here: int = 0
    declared_copies_first_ns: int | None = None
    declared_copies_last_ns: int | None = None
    #: Files directly inside whose address answered no file, as their listing or a remote reading
    #: of the address said, and none of whose bytes a remote reading read. Then how many answered
    #: each way (``not_found``, ``moved``, ``malformed``), in the states' order. A broken address
    #: says nothing about whether a file exists elsewhere.
    broken_addresses_here: int = 0
    broken_address_states: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class GroupResolution:
    """What a group a person approved resolved for one entry.

    For an entry beneath an experiment sharing an approved core set of terms, the resolution holds
    the group's key in that experiment and across the experiments. The key is None for a file
    carrying a word the core lacks, which is the deviation that is asked about. For the
    experiment's own folder, it also holds each deviation the folder raises with the entries it
    rests on, the folder's own children leading to the files.
    """

    within: str | None = None
    across: str | None = None
    deviations: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovedGroup:
    """One entry beside the approved group of experiments that holds it, if any."""

    entry: Entry
    #: The proposed group's name, as its question raised it, where a person approved it and one of
    #: its member experiments' folders lies above the entry; None otherwise.
    group: str | None = None
    #: The group an approved core set of terms resolved for the entry, if any.
    resolution: GroupResolution | None = None


@dataclass(frozen=True, slots=True)
class EntryObservations:
    """One entry beside what was read from its content.

    This is what a rule reads where an observation means something different on a file that a walk
    matched to one that left (``Entry.moved_by``). The readings are the departed file's until a
    dive checks them.
    """

    entry: Entry
    observations: tuple[Observation, ...] = ()


@dataclass(frozen=True, slots=True)
class AnsweredCopies:
    """One entry beside the set of copies a person answered it belongs to, if any."""

    entry: Entry
    #: The set, named by the class a person answered "Copies" about (the least target, where sets
    #: sharing a file are one set); None where no such answer holds the entry.
    copies: str | None = None


@dataclass(frozen=True, slots=True)
class ArchivedCopy:
    """One entry beside the real file an archive's link names, if any."""

    entry: Entry
    #: The real file's entry identifier: for a link, the file it stands for; for the file, itself.
    copy_of: str | None = None
    #: What matched them: ``name`` alone, or ``name and path`` where the link's text said a path,
    #: each ``and declared size`` where a listing's declared size of the file agreed.
    basis: str = ""
    #: The statements the match rests on beyond names: the listing's declared size, where read.
    evidence: tuple[str, ...] = ()
    #: Why a link names no file where a listing's declared size refused a candidate. A declared
    #: size decides nothing silently. The field is empty otherwise.
    refused: str = ""


@dataclass(frozen=True, slots=True)
class NameDates:
    """One entry beside the dates names give it: its own, and its folder's."""

    entry: Entry
    #: The dates the entry's own name holds, as the reader normalised them, in canonical order.
    own: tuple[str, ...] = ()
    #: The dates of the nearest folder above it whose name holds one, and that folder's path.
    inherited: tuple[str, ...] = ()
    folder: str | None = None


def template_key(name: str) -> str:
    """A file's name as one tool's batch writes it: its extension, and its first and last parts.

    A tracking batch run over every movie of a session writes ``Traj_<movie>.nd2_crop.csv`` for
    each. Where a movie held nothing to track (a brightfield image, a condition with no particles),
    the table is empty beside tables of the same template holding data. The movie names differ by a
    word and not a number, so :func:`sibling_key` cannot see them as siblings. The first part
    (``traj``) and the last (``crop``) are the tool's, and what lies between is the movie's. A name
    of fewer than three parts has no template.
    """
    stem, dot, extension = name.casefold().rpartition(".")
    if not (dot and stem.strip(".") and extension):
        stem, extension = name.casefold(), ""
    parts = [part for part in _TEMPLATE_SEPARATORS.split(stem) if part]
    if len(parts) < 3:
        return ""
    return f"{parts[0]}|{parts[-1]}|{extension}"


_NAME_DATE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?$")


def name_dates(components: Sequence[Component]) -> tuple[str, ...]:
    """The dates a name holds, as the reader normalised them: days and months, each once."""
    return tuple(
        sorted(
            {
                str(component.normalized_value)
                for component in components
                if component.component_type == "date"
                and _NAME_DATE.match(str(component.normalized_value))
            }
        )
    )


def sibling_key(name: str) -> str:
    """A file's name as its siblings share it: case folded, without its extension, every run of
    digits one ``#``.

    ``Traj_cell12.csv`` and ``Traj_cell3.csv`` share ``traj_cell#``. A replicate's outputs differ in
    their numbers and nothing else, which is what a person means by the same files. The extension is
    set aside too. A tracking tool can write each movie's results as ``.txt`` and as ``.csv``. Where
    it finds nothing, the table is empty beside a text file that holds bytes.
    """
    stem, dot, extension = name.rpartition(".")
    kept = stem if dot and stem.strip(".") and extension else name
    return _DIGITS.sub("#", kept.casefold())


@dataclass(frozen=True, slots=True)
class EntryRecords:
    """Everything the Index and the earlier phases hold about one entry, in canonical order."""

    entry: Entry
    components: tuple[Component, ...] = ()
    bindings: tuple[DictionaryBinding, ...] = ()
    observations: tuple[Observation, ...] = ()
    #: Claims the phases before this one made about the entry. Empty for the
    #: first phase, which is classification.
    claims: tuple[Claim, ...] = ()
    #: The record of the root the entry was found under, where a rule reads it.
    root: Root | None = None
    #: What went wrong reading this entry, where a rule in force reads a failure. Access and
    #: comprehension only: those are the scan layer's, recorded as facts about the filesystem and
    #: about coverage. An interpretation failure is a rule's own and is produced by the pass that
    #: would be reading it.
    failures: tuple[ScanError, ...] = ()
    #: The Dictionary's spellings of every sense, where a rule in force reads a name against
    #: them. The mapping is the same for every entry of a run, and empty otherwise.
    spellings: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Who writes each declared spelling of a format, by the sense's term, where a rule in force
    #: reads where a file can have come from. The mapping is the same for every entry of a run, and
    #: empty otherwise.
    origins: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    #: The name's own slots, as the component each holds, in the order they were written. They are
    #: loaded only where a rule in force keys on positions, because a name layout is one more table
    #: to read and memory is the figure to watch. A grouping rule that must tell ``tile_1_2`` from
    #: ``tile_2_1`` cannot do it from components, which are deduplicated by value and carry no
    #: position. The identifiers are the components' own, so a slot costs a pointer.
    slots: tuple[str, ...] = ()
    #: The workflows the project has declared and not withdrawn, on ``spellings``' precedent: the
    #: same tuple for every entry of a run, handed over and not fetched, because a predicate that
    #: could reach the project could write to it.
    #:
    #: A workflow is what says where data enters, so a rule asking whether a file is an
    #: observation or a derivation cannot answer from the stage word alone. The workflows are
    #: attached to the subjects the proposing and flagging phases read, which is where such a rule
    #: can live. A classifying rule cannot read another classifying rule's stage claim, and a
    #: grouping, proposing or flagging rule can.
    #:
    #: Empty means the project declared none, which is not the same fact as declaring the default.
    #: The reader decides what to do about it.
    workflows: tuple[Workflow, ...] = ()
    #: What the entry's neighbours hold, where a rule in force reads proximity: the sibling keys of
    #: the files holding bytes in its folder, and in the folders beside it. The frozen sets are the
    #: same for every entry of a folder, so a folder costs one pair of sets.
    beside: frozenset[str] = frozenset()
    nearby: frozenset[str] = frozenset()
    #: The batch templates of the files holding bytes in its folder.
    templates: frozenset[str] = frozenset()
    #: The names of the files holding bytes in its folder, as written but for case, with numbers
    #: and extension kept. A rule uses them to ask whether a sibling is this file at an earlier step
    #: (``X.bam`` beside ``X.aligned.bam``), which a key masking numbers cannot tell from another
    #: replicate. There is one frozen set per folder, shared by its files.
    beside_names: frozenset[str] = frozenset()
    #: What a folder holds, where a rule in force reads it. It is None for a file.
    contents: FolderContents | None = None
    #: The approved group of experiments holding this entry, where a rule reads it.
    approved_group: str | None = None
    #: The group an approved core set of terms resolved for this entry.
    group_resolution: GroupResolution | None = None
    #: The set of copies a person answered holds this entry, where a rule reads it.
    answered_copies: str | None = None
    #: The core sets of terms a person declined as groups (a reject on ``group:<terms>``), handed to
    #: each folder so a proposal of that core is not asked again.
    declined_groups: frozenset[str] = frozenset()
    #: Which word the lattice sees for a bound spelling (the setting ``groups.words_as``): ``terms``
    #: for its Dictionary term, or ``spellings``. The value is the same for every entry of a run,
    #: handed over on ``declined_groups``' precedent so a predicate reads the project's setting
    #: without reaching the project (``ingence.grouping.lattice_words``).
    words_as: str = "terms"
    #: The real file an archive's link stands for, where a rule reads it: for the link, that file's
    #: identifier; for the file, its own; with what matched them.
    archived_copy_of: str | None = None
    archived_copy_basis: str = ""
    #: The observations the match cites beyond names: a listing's declared size.
    archived_copy_evidence: tuple[str, ...] = ()
    #: Why a listing's declared size left a link matching no file, where it did.
    archived_copy_refused: str = ""
    #: The dates the nearest dated folder above it names, and that folder's path.
    folder_dates: tuple[str, ...] = ()
    dated_folder: str | None = None
    #: True for a folder a person said is not an installed program or a package, read from the
    #: Record. The rule raising a folder as one leaves it alone.
    not_a_program: bool = False
    #: True for a folder a person said is a program, read from the Record: the answer to a question
    #: about software outside the catalogue, which puts the folder in program scope.
    a_program: bool = False
    #: Where a file lies in the experiment the experiment rules concluded above it, handed to the
    #: grouping phase where a rule in force compares names within an experiment. It is None for a
    #: file beneath no experiment, and otherwise.
    experiment_place: ExperimentPlace | None = None
    #: The acquisition a file is named for, or, for an acquisition, itself, handed to the grouping
    #: phase where a rule in force groups files by the acquisition they are named for.
    named_acquisition: NamedAcquisition | None = None
    #: The class of a series of numbered files and the table named for it beside them, and the
    #: class of one name in sibling folders, a file is in. It is handed to the grouping phase where
    #: a rule in force groups by them.
    named_series: str | None = None
    named_beside: str | None = None
    #: The derivations proposed into this file, each with its source's stages, handed to the phase
    #: after proposing, where a rule reads whether a derivation and a stage agree.
    derived_from: tuple[Derivation, ...] = ()

    def narrowed(self, reads: Reads) -> object:
        """Return the part of these records a predicate declared it reads.

        Refuses a shape that is not entry-shaped and does not hand over something the predicate
        did not ask for. A predicate reading a pair that received one entry would narrow it to
        nothing and abstain, and that abstention would look exactly like a verdict.
        """
        if reads is Reads.ENTRY:
            return self.entry
        if reads is Reads.COMPONENTS:
            return self.components
        if reads is Reads.BINDINGS:
            return self.bindings
        if reads is Reads.OBSERVATIONS:
            return self.observations
        if reads is Reads.RECORDS:
            return self
        if reads is Reads.ENTRY_OBSERVATIONS:
            return EntryObservations(entry=self.entry, observations=self.observations)
        if reads is Reads.ENTRY_IN_ROOT:
            return EntryInRoot(entry=self.entry, root=self.root)
        if reads is Reads.NAME_SPELLINGS:
            return NameSpellings(entry=self.entry, spellings=self.spellings)
        if reads is Reads.FORMAT_ORIGIN:
            return FormatOrigins(
                entry=self.entry,
                components=self.components,
                bindings=self.bindings,
                origins=self.origins,
            )
        if reads is Reads.ENTRY_AMONG_SIBLINGS:
            return EntryAmongSiblings(
                entry=self.entry, beside=self.beside, nearby=self.nearby, templates=self.templates
            )
        if reads is Reads.FOLDER_CONTENTS:
            return self.contents if self.contents is not None else FolderContents(entry=self.entry)
        if reads is Reads.NAME_DATES:
            return NameDates(
                entry=self.entry,
                own=name_dates(self.components),
                inherited=self.folder_dates,
                folder=self.dated_folder,
            )
        if reads is Reads.ARCHIVED_COPY:
            return ArchivedCopy(
                entry=self.entry,
                copy_of=self.archived_copy_of,
                basis=self.archived_copy_basis,
                evidence=self.archived_copy_evidence,
                refused=self.archived_copy_refused,
            )
        if reads is Reads.APPROVED_GROUP:
            return ApprovedGroup(
                entry=self.entry, group=self.approved_group, resolution=self.group_resolution
            )
        if reads is Reads.NAMED_ACQUISITION:
            return NamedFor(
                entry=self.entry,
                named_acquisition=self.named_acquisition,
                named_series=self.named_series,
                named_beside=self.named_beside,
            )
        if reads is Reads.DERIVED_FROM:
            return DerivedFrom(entry=self.entry, claims=self.claims, derivations=self.derived_from)
        if reads is Reads.ANSWERED_COPIES:
            return AnsweredCopies(entry=self.entry, copies=self.answered_copies)
        message = f"one entry's records cannot be narrowed to {reads.value!r}"
        raise ValueError(message)

    def carrying(self, values: Collection[str], reads: Reads) -> tuple[str, ...]:
        """Return the identifiers of the handed records that carry a concluded value.

        These are what a claim lists as its inputs, so that a conclusion walks back to the records
        it rests on mechanically: a binding whose term is the value, a component whose normalised
        value is it, an observation whose text is it, or the entry itself for a reading of the
        entry.
        """
        wanted = frozenset(values)
        found: set[str] = set()
        if reads in {
            Reads.ENTRY,
            Reads.ENTRY_IN_ROOT,
            Reads.NAME_SPELLINGS,
            Reads.ENTRY_AMONG_SIBLINGS,
            Reads.FOLDER_CONTENTS,
            Reads.APPROVED_GROUP,
            Reads.ARCHIVED_COPY,
            Reads.NAME_DATES,
            Reads.NAMED_ACQUISITION,
            Reads.DERIVED_FROM,
            Reads.ANSWERED_COPIES,
        }:
            found.add(self.entry.entry_id)
        if reads in {Reads.BINDINGS, Reads.RECORDS, Reads.FORMAT_ORIGIN}:
            found.update(
                binding.binding_id
                for binding in self.bindings
                if binding.resolution_status is ResolutionStatus.RESOLVED and binding.term in wanted
            )
        if reads in {Reads.COMPONENTS, Reads.RECORDS}:
            found.update(
                component.component_id
                for component in self.components
                if component.normalized_value in wanted
            )
        if reads in {Reads.OBSERVATIONS, Reads.RECORDS, Reads.ENTRY_OBSERVATIONS}:
            found.update(
                observation.observation_id
                for observation in self.observations
                if observation.value_text in wanted
            )
        return tuple(sorted(found))


@dataclass(frozen=True, slots=True)
class ExperimentPlace:
    """Where one file lies in the experiment the experiment rules concluded above it.

    This is what a grouping rule comparing names within an experiment reads beside a file's own
    records: the experiment's folder, the claims that concluded it one, and the folders between it
    and the file, outermost first, as their own records. The predicate reads what each is called by
    the Dictionary's senses (a stage subfolder's name is left out as a stage word is). A claim about
    the file may cite none of these records except the experiment's claims, because the folders'
    records are about other entries.
    """

    #: The experiment folder's entry identifier.
    experiment: str
    #: The claims concluding that folder an experiment, which a claim keyed on it cites.
    claims: tuple[str, ...] = ()
    #: The folders between the experiment's folder and the file, outermost first.
    between: tuple[EntryRecords, ...] = ()


@dataclass(frozen=True, slots=True)
class NamedAcquisition:
    """The acquisition a file's name holds whole, nearest it in its experiment.

    This is what a grouping rule reading which acquisition a file is named for reads. A tool names
    what it writes after the movie it read, with words of its own beside it, which a name identity
    cannot join. For an acquisition, the value is itself.
    """

    #: The acquisition's entry identifier.
    acquisition: str
    #: The claim concluding it an acquisition, which a claim keyed on it cites.
    claim: str
    #: How many letters and digits the acquisition's name held has, so a rule naming a larger
    #: minimum than the pass matched at reads its own.
    characters: int = 0
    #: The file it was made from where that is not the acquisition: another file whose whole name,
    #: stem and extension, its name holds, itself named for the acquisition. It is empty where the
    #: acquisition is its source.
    source: str = ""
    #: The file it was made from where its name is that file's, beside it, and a word meaning an
    #: annotation: ``vol07_labels.nii`` from ``vol07.nii``. It is empty otherwise.
    annotates: str = ""
    #: How many folders above its own the file whose whole name it holds was found, where it was
    #: found by looking upward; 0 otherwise.
    levels_up: int = 0


@dataclass(frozen=True, slots=True)
class NamedFor:
    """One entry beside the acquisition its name holds whole, if any, and the class of a series
    summarised beside it and of one name in sibling folders it is in."""

    entry: Entry
    named_acquisition: NamedAcquisition | None = None
    named_series: str | None = None
    named_beside: str | None = None


@dataclass(frozen=True, slots=True)
class Derivation:
    """One derivation proposed into a file, as the phase after proposing reads it."""

    #: The candidate proposing it, and the rule that did.
    candidate_id: str
    rule_id: str
    #: The file it was made from, by its identifier and its name.
    source_id: str
    source_name: str
    #: The stages the source holds, each with the claim concluding it, in canonical order.
    source_stages: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DerivedFrom:
    """One file beside its own claims and the derivations proposed into it."""

    entry: Entry
    claims: tuple[Claim, ...] = ()
    derivations: tuple[Derivation, ...] = ()


@dataclass(frozen=True, slots=True)
class PairRecords:
    """Two entries drawn from one class of an index, the source first.

    This is what a proposing or constraining predicate is handed. Each end carries its own records
    and the claims the earlier phases published about it, which is where a stage or a role is read.
    Beside them go the index the pair was drawn within and what placed each end in its class, so
    support can cite why the two were ever compared.
    """

    source: EntryRecords
    target: EntryRecords
    #: The index the pair was drawn within: a grouping rule's identifier, ``parent``,
    #: ``mentioned`` or ``co_mentioned``.
    within: str
    #: Each end's claim into its class, the containment edge from its folder, or the
    #: observation of the mention that placed it.
    source_membership: str
    target_membership: str
    #: The resolved mentions the pair was drawn from, where its index is a resolution: the source's
    #: mentions of the target for ``mentioned``, and the describing file's mentions of both ends
    #: for ``co_mentioned``. Empty otherwise.
    resolutions: tuple[Resolution, ...] = ()
    #: The file whose mentions named both ends, for ``co_mentioned``; None otherwise.
    describing: EntryRecords | None = None
    #: Every member of the class the pair was drawn within, both ends among them, where the index
    #: is a grouping's class; empty for a folder or a resolution. It says whether a rung is absent
    #: from an identity, so a step may skip it. The tuple is the same for every pair of one class.
    #: A candidate may never cite it: it is a fact about the class and not about an end.
    members: tuple[EntryRecords, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """One subject and the counterparts an index offers it, in canonical order.

    This is what a bound on candidate generation is handed: enough to say how many counterparts to
    cut, and nothing about the pairs themselves, so a cut is on count and never on merit.
    """

    subject: EntryRecords
    #: The counterparts are in canonical order. The sequence is a view over the class and not a copy
    #: of it, so a bound reads its length and its members and never holds the class once per
    #: subject.
    counterparts: Sequence[EntryRecords]
    within: str


@dataclass(frozen=True, slots=True)
class ScopeRecords:
    """One region of the pool and every entry it holds, each with its own records.

    This is what a flagging rule reading a scope is handed. A finding about a scope is a fact
    nothing in one file could carry: that a label recurs across folders, that a folder fails far
    more often than the pool, that a region's names reach no sense. Raising such a thing per entry
    would say it once per file, which is one question asked thousands of times, and would attribute
    to a file a fact that is not about it.

    A scope costs no second load. The pass assembles every entry's records once for the whole run,
    and a scope is a view over exactly those objects. The entries are the same instances the
    classifying and grouping phases were handed, in the same canonical order. What a scope adds is
    the grouping and its identity, not a copy.

    There is a scope per root and a scope per folder. A root holds every entry beneath it, so a rule
    can see across folders. A folder holds the entries directly in it, so a rule can say where a
    thing is.
    """

    scope: Scope
    #: Every entry the scope holds, in the pass's canonical order.
    entries: tuple[EntryRecords, ...] = ()
    #: The folder's own records, for a folder scope. A rule reading a region can also read what the
    #: region is called, which is how an application bundle is recognised by the name packaging
    #: gave it. The field is None for a root, because a root has no entry beyond its own folder.
    #: A finding about the scope may never cite it (:meth:`owns`).
    holder: EntryRecords | None = None
    #: Every entry in the run, where a rule in force compares a region against the pool it is part
    #: of. The field holds the pool's entries and not the pool's totals, so a rule computing a rate
    #: computes it the same way twice and not against a figure somebody else aggregated. This is one
    #: definition of what failing means, applied to two populations. The same tuple is handed to
    #: every scope, so it costs one object and a reference each.
    #:
    #: A finding may never cite it. :meth:`owns` reads ``entries`` alone, so a finding about one
    #: region still rests only on that region's own records.
    pool: tuple[EntryRecords, ...] = ()
    #: What lies beside the folder, for a folder scope: every entry its parent holds, the folder
    #: itself among them, as the parent's own scope holds them. A rule reading a region may read
    #: what its neighbours are called (a package shared as an archive of its own name lies beside
    #: it, not in it) and may never cite one (:meth:`owns`).
    siblings: tuple[EntryRecords, ...] = ()
    #: The programs and packages the Dictionary declares by their layouts, handed to every scope
    #: where a rule in force declares it reads them
    #: (:class:`~ingence.rulebook.registry.VocabularyRead`), so the predicate never reaches the
    #: Dictionary. The tuple is the same for every scope.
    layouts: tuple[ProgramLayout, ...] = ()
    #: Every spelling of each shipped domain pack the project does not load, by domain, handed to a
    #: root scope where a rule in force declares it reads them.
    unloaded_packs: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: Each of those packs' spellings an instrument writes as it acquires, handed beside them: files
    #: of one alone propose the pack.
    unloaded_instruments: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: Each shipped pack that the project loads, when it loads every pack, and that nobody has
    #: answered about, with its senses' term identifiers and the spellings it adds to the core's
    #: types. The ones that no binding names are asked about.
    idle_packs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def token(self) -> str:
        """Return the scope's canonical string form, which is the finding's subject."""
        return self.scope.token

    def owns(self) -> frozenset[str]:
        """Return every record identifier the scope's own entries carry.

        These are what a finding about a scope may cite. They are wider than one entry's own
        records and still bounded by the scope: a finding about one region resting on a record from
        another would carry a fact across regions, which is the same thing
        :func:`~ingence.infer.evaluate.raise_on_entry` refuses one entry away.
        """
        owned: set[str] = set()
        for records in self.entries:
            owned.add(records.entry.entry_id)
            owned.update(component.component_id for component in records.components)
            owned.update(binding.binding_id for binding in records.bindings)
            owned.update(observation.observation_id for observation in records.observations)
            owned.update(claim.claim_id for claim in records.claims)
        return frozenset(owned)
