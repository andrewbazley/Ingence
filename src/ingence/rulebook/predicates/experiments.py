"""Where an experiment's folder is: by what it holds first, by its name second.

Two predicates find an experiment's folder, and each concludes on the folder itself under its own
rule. The first reads the structure of the folder's contents. Does the folder contain raw and
analyzed data from the same groups or experiments? Does the folder's title represent all of its
contents? If so, the folder is probably an experiment folder. The second reads the folder's name.
The policy reads the content structure first and the name second, so the name takes effect only
where the structure does not decide.

By what it holds (:class:`ExperimentFromContents`). A folder is where one experiment's material
is bound together when that material is held here and not above:

- held here: it holds directly at least as many acquisitions, or files named for one, as it has
  subfolders holding such material (so one stray movie among fifty experiments' folders does not
  make their parent an experiment), or its subfolders holding such material are all linked to one
  another by the acquisition names they share and none of them is complete (one experiment split
  into ``raw``, ``tiff`` and ``analysis`` folders is linked and each part partial; a month or year
  folder of separate experiments holds complete ones, however many names they happen to share);
- not above: no folder above it has files of its own that hold or are named for the acquisitions
  beneath it (a reanalysis that copied the movies two folders down is still part of their
  experiment), its parent does not hold such material directly in the measure above (a subfolder of
  beads beside fifty movies is part of their experiment), and its parent's subfolders are not one
  experiment split in the way above.

What it concludes says what the material is: ``experiment`` where acquisitions lie beneath with
files named for them (raw and analyzed of the same groups); ``acquisitions`` where only acquisitions
do (one session's raw data, its analysis elsewhere or not done); ``analysis`` where only files named
for acquisitions held elsewhere do (a reanalysis, or tracks prepared for someone). Nothing here
reads what a word means. The counts are of declared formats and of names held whole inside other
names.

By its name (:class:`ExperimentFromDatedName`): the shallowest folder whose name holds a date and
a word, as the tokenizer read them. A folder named by a year or a month alone holds experiments and
is not one, and a folder inside a dated one is part of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.schema import DictionaryBinding, EntryType, RuleEffect
from ingence.core.tiers import ResolutionStatus
from ingence.grouping.experiment_links import (
    DEFAULT_POLICY,
    ProposedGroup,
    experiment_rows,
    experiment_units,
    propose_groups,
)
from ingence.grouping.identifiers import NOT_GROUP_WORD_KINDS, replicate_labels_of
from ingence.grouping.lattice_words import TERMS, WordMap, lattice_word
from ingence.grouping.resolutions import (
    ASKED,
    DEFAULT_MAXIMUM_CONCEPTS,
    DEFAULT_MINIMUM_EXPERIMENTS,
    DERIVATION_MODES,
    EACH_DEVIATION,
    FEWEST_EXPERIMENTS,
    LATTICE,
    NOT_ASKED,
    Lattice,
    ScopedAnswer,
    group_lattice,
    lattice_terms,
    resolve_by_lattice,
    resolve_groups,
)
from ingence.infer.subjects import EntryRecords, FolderContents, GroupResolution
from ingence.rulebook.registry import (
    Abstention,
    ClassKey,
    Concluded,
    PredicateNotEvaluableError,
    Raised,
    Reads,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.rulebook.registry import Predicate

__all__ = [
    "RESOLUTION_DEFAULTS",
    "RESOLUTION_PREDICATES",
    "AnswerReader",
    "ExperimentFromContents",
    "ExperimentFromDatedName",
    "GroupDeviation",
    "ResolvedGroupAcrossExperiments",
    "ResolvedGroupWithinExperiment",
    "bound_above",
    "bound_here",
    "experiment_folders",
    "experiment_terms",
    "group_resolutions",
    "word_map",
]


def _holds_directly(material: int, holding_children: int) -> bool:
    """Whether a folder's own files are its material, rather than strays among its subfolders'.

    A folder holding directly at least as many acquisitions, or files named for one, as it has
    subfolders holding such material is where that material is kept (fifty movies and one folder of
    beads); one stray movie among fifty experiments' folders is not.
    """
    return material > 0 and material >= holding_children


def bound_here(contents: FolderContents) -> bool:
    """Whether an experiment's material is held together in this folder.

    Or one run's outputs, one subfolder per item: a folder whose subfolders each hold one item's
    files, written from one template, holds that run's material together. The folder that holds a
    run's output folder also holds the material together, where that output folder is the only
    subfolder holding material. A tool writes its per-item folders into one output folder inside
    the folder the run was set up in, which is the experiment's.
    """
    if contents.item_children:
        return True
    if _holds_directly(contents.acquisitions_here + contents.named_here, contents.holding_children):
        return True
    if contents.holding_children == 1 and contents.item_holding_children == 1:
        return True  # (where it is bound further up, :func:`bound_above` says so)
    return (
        contents.holding_children >= 2
        and contents.linked_children == contents.holding_children
        and contents.whole_children == 0
    )


def bound_above(contents: FolderContents) -> bool:
    """Whether the folder's parent holds the same material together, so the folder is a part.

    A tool can write many per-movie folders from one template, each holding a tracks table and a
    plot. Each such folder is a part, because the folders are their parent's outputs. A run's
    output folder is also a part of the folder holding it, where it is that folder's only
    subfolder holding material: the run's experiment is one folder up.
    """
    if contents.shared_above or contents.one_item_of_many:
        return True
    if (
        contents.item_children
        and contents.parent_holding_children == 1
        and contents.entry.parent_entry_id is not None
        # Unless that folder is itself bound further up: then the run's own output folder stays its
        # unit.
        and not (contents.parent is not None and bound_above(contents.parent))
    ):
        return True
    if _holds_directly(contents.parent_material_here, contents.parent_holding_children) and bool(
        contents.acquisitions_beneath or contents.named_beneath
    ):
        return True
    return (
        contents.parent_holding_children >= 2
        and contents.parent_linked_children == contents.parent_holding_children
        and contents.parent_whole_children == 0
        and bool(contents.acquisitions_beneath or contents.named_beneath)
    )


class ExperimentFromContents:
    """A folder where one experiment's material is bound together, by what the folder holds."""

    #: Version 2: a run's per-item folders holding the same files, and the folder holding the
    #: run's output folder.
    name = "experiment_from_contents"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return what the folder's material is, or None where it is not bound here."""
        del parameters
        if (
            not isinstance(subject, FolderContents)
            or subject.entry.entry_type != EntryType.DIRECTORY
        ):
            return None
        if not bound_here(subject) or bound_above(subject):
            return None
        if subject.acquisitions_beneath and subject.named_beneath_own:
            kind = "experiment"
        elif subject.acquisitions_beneath:
            kind = "acquisitions"
        elif subject.named_beneath or subject.items_beneath:
            # Files named for acquisitions held elsewhere, or a run's per-item outputs naming none.
            kind = "analysis"
        else:
            return None
        return Concluded(values=(kind,), inputs=(subject.entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say which of the two tests the folder failed."""
        del parameters
        entry_id = getattr(getattr(subject, "entry", None), "entry_id", "")
        inputs = (entry_id,) if entry_id else ()
        if (
            not isinstance(subject, FolderContents)
            or subject.entry.entry_type != EntryType.DIRECTORY
        ):
            return Abstention(inputs=inputs, reason="not a folder")
        if not (subject.acquisitions_beneath or subject.named_beneath or subject.items_beneath):
            return Abstention(
                inputs=inputs,
                reason="holds no acquisition, no file named for one and no run's per-item outputs",
            )
        if not bound_here(subject):
            return Abstention(
                inputs=inputs,
                reason="its material lies in separate subfolders (not linked, or one complete)",
            )
        return Abstention(inputs=inputs, reason="its parent holds the same material together")


class ExperimentFromDatedName:
    """The shallowest folder whose name holds a date and a word, as the tokenizer read them.

    A folder that is one item's folder among a run's is never an experiment (version 2). A tool
    that writes one folder per item names each after its item, and a movie's name often holds a
    date. So without this check, each of a run's many per-item folders would be an experiment of its
    a part of the run where its siblings share its name's words
    (:attr:`~ingence.infer.subjects.FolderContents.one_item_of_a_family`), whatever its date. A
    folder of a run's items named by different words keeps its dated name.
    """

    name = "experiment_from_dated_name"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return ``experiment`` for the shallowest dated folder on its path, else None."""
        del parameters
        if (
            not isinstance(subject, FolderContents)
            or subject.entry.entry_type != EntryType.DIRECTORY
        ):
            return None
        if subject.name_dated and not subject.dated_ancestors and not subject.one_item_of_a_family:
            return Concluded(values=("experiment",), inputs=(subject.entry.entry_id,))
        return None

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the name holds no date and word, a folder above it does, or it is one
        item's folder among a run's."""
        del parameters
        entry_id = getattr(getattr(subject, "entry", None), "entry_id", "")
        inputs = (entry_id,) if entry_id else ()
        if isinstance(subject, FolderContents) and subject.name_dated:
            if subject.one_item_of_a_family:
                return Abstention(
                    inputs=inputs, reason="it is one item's folder among a run's per-item outputs"
                )
            return Abstention(inputs=inputs, reason="a folder above it already has a dated name")
        return Abstention(inputs=inputs, reason="its name holds no date beside a word")


@declare(
    "experiment_from_contents",
    version="2",
    summary="Classify a folder where one experiment's material is bound together, by what it holds",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.FOLDER_CONTENTS,
    parameters=(
        "minimum_alike_items",
        "minimum_items",
        "minimum_key_characters",
        "most_folders_a_date",
    ),
    support_families=("filesystem_fact", "name_structure"),
)
def _build_experiment_from_contents() -> Predicate:
    return ExperimentFromContents()


@declare(
    "experiment_from_dated_name",
    version="2",
    summary="Classify the shallowest folder whose name holds a date and a word as an experiment",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.FOLDER_CONTENTS,
    parameters=(),
    support_families=("name_structure",),
)
def _build_experiment_from_dated_name() -> Predicate:
    return ExperimentFromDatedName()


class ExperimentsMayBelongTogether:
    """Experiments a deterministic clustering proposes as belonging together, raised as questions.

    A proposal is never a claim. Automatic grouping proposes named groups, and the proposals must
    not result in hundreds of named groups. A group is approved at the highest level where that
    group appears, and the approval is inherited beneath. So each proposed group is one value,
    raised on each member experiment's folder. The Review queue asks one question per value and
    gathers the folders raising it as its members. Nothing is concluded until a person answers.
    Approving the group approves every file beneath its members.

    The clustering reads the whole pool once per run. Every folder scope carries the same pool, so
    the proposal is computed on the first folder and kept for the rest, keyed by that pool object
    and the parameters.

    The experiments are the folders that the two experiment rules concluded on
    (``experiment_rules``: the structure rule, then the name rule), made one set by ``policy``
    (:func:`~ingence.grouping.experiment_links.experiment_units`). The features and the clustering
    are :mod:`ingence.grouping.experiment_links`.
    """

    name = "experiments_may_belong_together"
    #: Version 2: stage and connecting words link nothing, a small pool's replicate days link, and
    #: a proposal of a set of words that the lattice asks is left to the lattice.
    #: Version 3: a date's or a replicate's word links nothing, and a set that too few experiments
    #: share to ask is left to the lattice too.
    #: Version 4: a bound word is its Dictionary term (``groups.words_as``).
    version = "4"

    def __init__(self) -> None:
        self._source: object = None
        self._key: tuple[object, ...] = ()
        self._members: dict[str, tuple[str, ProposedGroup, tuple[str, ...], str]] = {}

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the group the scope's folder is proposed into, citing the folder."""
        holder = getattr(subject, "holder", None)
        if holder is None:
            return None
        if getattr(getattr(holder, "group_resolution", None), "across", None):
            # Already answered: the experiment shares a core that a person approved, so a group it
            # is proposed into again, such as a new replicate day among its members, is not asked.
            # A new word is asked, as a deviation.
            return None
        found = self._membership(subject, parameters).get(holder.entry.relative_path)
        if found is None:
            return None
        value, group, core, lattice_core = found
        if core and " ".join(core) in getattr(holder, "declined_groups", frozenset()):
            return None  # a person said these do not belong together: not asked again
        resolution = getattr(holder, "group_resolution", None)
        if lattice_core and lattice_core in getattr(resolution, "lattice_sets", frozenset()):
            # Under the lattice, a set of words is asked in its turn. The set that the members
            # share, read as the lattice reads terms, is one that the lattice asks, waits to ask,
            # keeps it. Where the lattice's bound was reached, it is still proposed here.
            return None
        others = [m.rsplit("/", 1)[-1] for m in group.members if m != holder.entry.relative_path]
        # A finding about a folder cites what the folder holds, never the folder itself.
        held = sorted(records.entry.entry_id for records in getattr(subject, "entries", ()))[:3]
        if not held:
            return None
        return Raised(
            values=(value,),
            inputs=tuple(held),
            reason=(
                f"proposed with {len(others)} other experiments sharing "
                f"{', '.join(f.split(':', 1)[1] for f in group.name[:4]) or 'files'}: "
                + "; ".join(others[:8])
                + ("; ..." if len(others) > 8 else "")
                + (f"; core terms: {' '.join(core)}" if core else "")
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether the folder is not an experiment or no proposed group holds it."""
        return Abstention(reason="the folder is not an experiment in any proposed group")

    def _membership(
        self, subject: object, parameters: Mapping[str, object]
    ) -> dict[str, tuple[str, ProposedGroup, tuple[str, ...], str]]:
        source = getattr(subject, "pool", None) or getattr(subject, "entries", None) or ()
        pool: tuple[object, ...] = tuple(source)
        key = tuple(sorted((str(k), str(v)) for k, v in parameters.items()))
        if source is self._source and key == self._key:
            return self._members
        members: dict[str, tuple[str, ProposedGroup, tuple[str, ...], str]] = {}
        held = [item for item in pool if isinstance(item, EntryRecords)]
        found = _proposals(held, parameters)
        if found is not None:
            groups, _beyond, _keys = found
            cores = _group_cores(held, parameters, groups)
            # One reading of terms: the set that the lattice would ask of a group's members is
            # the one its own terms give, handed on each experiment's folder.
            read: dict[str, frozenset[str]] = {
                item.entry.relative_path: item.group_resolution.lattice_terms
                for item in held
                if isinstance(item.group_resolution, ScaledResolution)
                and item.group_resolution.lattice_terms
            }
            for group, core in zip(groups, cores, strict=True):
                value = _group_value(group)
                shared = [read[m] for m in group.members if m in read]
                lattice_core = (
                    " ".join(sorted(frozenset.intersection(*shared)))
                    if shared and len(shared) == len(group.members)
                    else ""
                )
                for member in group.members:
                    members[member] = (value, group, core, lattice_core)
        # The pool object itself is kept, so its identity cannot be taken by another run's.
        self._source, self._key, self._members = source, key, members
        return members


def proposed_group_cores(
    entries: Sequence[EntryRecords], parameters: Mapping[str, object]
) -> list[tuple[str, ProposedGroup, tuple[str, ...]]]:
    """Every proposed group with the core set of terms its question offers.

    A page answers a proposal on its core (``group:<core>``, ``record/proposals.py``), so the
    rule that reads approved proposals finds each by its core, computed here by the one definition
    that its question's reason names it with.
    """
    held = list(entries)
    found = _proposals(held, parameters)
    if found is None:
        return []
    groups, _beyond, _keys = found
    cores = _group_cores(held, parameters, groups)
    return [(_group_value(group), group, core) for group, core in zip(groups, cores, strict=True)]


def _group_cores(
    entries: Sequence[EntryRecords],
    parameters: Mapping[str, object],
    groups: Sequence[ProposedGroup],
) -> list[tuple[str, ...]]:
    """Each proposed group's core set of terms: those every member experiment holds.

    Read as the group resolutions read an experiment's terms (``RESOLUTION_DEFAULTS``), so the
    core a person approves (``group:<terms>``, :func:`~ingence.record.targets.group_target`) is
    one every member already shares exactly or as a superset.
    """
    if not groups:
        return []
    merged: dict[str, object] = {**RESOLUTION_DEFAULTS, **parameters}
    units, folders = experiment_folders(entries, merged)
    terms, _placed, _words = experiment_terms(entries, merged, units=units, folders=folders)
    by_path: dict[str, list[frozenset[str]]] = {}
    for entry_id, path in units.items():
        by_path.setdefault(path, []).append(terms.get(entry_id, frozenset()))
    cores: list[tuple[str, ...]] = []
    for group in groups:
        held = [found for member in group.members for found in by_path.get(member, ())]
        shared = frozenset.intersection(*held) if held else frozenset()
        cores.append(tuple(sorted(shared)))
    return cores


def _group_value(group: ProposedGroup) -> str:
    words = list(
        dict.fromkeys(
            feature.split(":", 1)[1]
            for feature in group.name
            if not feature.startswith(("p:", "n:"))
        )
    )
    first = group.members[0].rsplit("/", 1)[-1]
    return f"{' '.join(words[:3]) or 'shared files'} [{first} and {len(group.members) - 1} more]"


def _experiments(
    entries: Sequence[EntryRecords], parameters: Mapping[str, object]
) -> tuple[dict[str, str], dict[str, EntryRecords]]:
    """The experiments' folders (path to entry identifier) and every folder's records, by id.

    The folders the two experiment rules concluded on, made one set by ``policy``: the one
    reading the clustering and the group resolutions share.
    """
    rules = [str(rule) for rule in _names(parameters.get("experiment_rules"))]
    if len(rules) != 2:
        message = (
            "experiments_may_belong_together names two rules: the one concluding by what a folder "
            "holds, then the one concluding by its name"
        )
        raise PredicateNotEvaluableError(message)
    structure: dict[str, str] = {}
    named: dict[str, str] = {}
    folders: dict[str, EntryRecords] = {}
    for held in entries:
        entry = held.entry
        if entry.entry_type != EntryType.DIRECTORY:
            continue
        folders[entry.entry_id] = held
        for claim in held.claims:
            if str(claim.resolution_status) != "resolved" or claim.object_reference is None:
                continue
            if claim.rule_id == rules[0]:
                structure[entry.relative_path] = str(claim.object_reference)
            elif claim.rule_id == rules[1]:
                named[entry.relative_path] = entry.entry_id
    ids = {held.entry.relative_path: held.entry.entry_id for held in folders.values()}
    chosen = experiment_units(
        structure, named, policy=str(parameters.get("policy", DEFAULT_POLICY))
    )
    return {path: ids[path] for path in chosen if path in ids}, folders


def experiment_folders(
    entries: Sequence[EntryRecords], parameters: Mapping[str, object]
) -> tuple[dict[str, str], dict[str, EntryRecords]]:
    """The experiments' folders by entry identifier (to their paths), and every folder's records.

    Keyed by identifier and never by path: two roots may each be a folder ``.``, and each is its
    own experiment. The set is the one ``policy`` makes of the two rules' folders.
    """
    rules = frozenset(str(rule) for rule in _names(parameters.get("experiment_rules")))
    paths, folders = _experiments(entries, parameters)
    chosen = set(paths)
    units = {
        entry_id: held.entry.relative_path
        for entry_id, held in folders.items()
        if held.entry.relative_path in chosen
        and any(
            claim.rule_id in rules
            and str(claim.resolution_status) == "resolved"
            and claim.object_reference is not None
            for claim in held.claims
        )
    }
    return units, folders


def _proposals(
    entries: Sequence[EntryRecords], parameters: Mapping[str, object]
) -> tuple[list[ProposedGroup], int, dict[str, str]] | None:
    units, folders = _experiments(entries, parameters)
    if len(units) < 2:
        return None
    unit_of_folder = {entry_id: path for path, entry_id in units.items()}
    # A stage word links no experiments. Two unrelated experiments under one year's folder, each
    # split into raw, processed and analysis, would be one proposal named by a stage. A word bound
    # to a sense of ``ignored_theme_kinds`` is not a feature, as it is not a term of a group
    # (version 3; an earlier rule declares none).
    ignored = frozenset(_names(parameters.get("ignored_theme_kinds")))

    def said(held: EntryRecords) -> list[str]:
        # Nor a date's or a replicate's word, as with the lattice's terms.
        left_out = _left_out(held, ignored | NOT_GROUP_WORD_KINDS)
        # A bound word is its Dictionary term here too (``groups.words_as``): the clustering and
        # the lattice read one set of words. Under ``spellings`` the value is the name's.
        terms = _terms_of(held)
        return [
            terms.get(c.component_id, c.normalized_value)
            for c in held.components
            if c.component_type == "word" and c.component_id not in left_out
        ]

    experiments = {}
    for path, entry_id in units.items():
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        experiments[path] = (said(folders[entry_id]), parent)
    files = []
    # A name's dates as the run's tokenizer stored them: the classifier masks them there.
    dates_of: dict[str, tuple[str, ...]] = {}
    # And the labels the run binds to the replicate kind (``rep``, ``trial``): their numbers count.
    labels: set[str] = set()
    nearest: dict[str | None, str | None] = {}
    for held in entries:
        entry = held.entry
        if entry.entry_type not in (EntryType.FILE, EntryType.SYMLINK):
            continue
        key = _nearest_unit(entry.parent_entry_id, unit_of_folder, folders, nearest)
        if key is None:
            continue
        files.append((key, said(held), entry.name))
        labels.update(replicate_labels_of(held.components, held.bindings))
        dates_of.setdefault(
            entry.name,
            tuple(
                str(c.raw_value)
                for c in held.components
                if c.component_type == "date" and c.raw_value
            ),
        )
    rows = experiment_rows(experiments, files, dates_of=dates_of, replicate_labels=labels)
    groups, beyond = propose_groups(
        rows,
        neighbours=int(str(parameters.get("neighbours", 3))),
        minimum_similarity=float(str(parameters.get("minimum_similarity", 0.2))),
        maximum_group_size=int(str(parameters.get("maximum_group_size", 8))),
        maximum_groups=int(str(parameters.get("maximum_groups", 0))) or None,
        common_share=float(str(parameters.get("common_share", 0.25))),
        minimum_shared=int(str(parameters.get("minimum_shared", 2))),
    )
    return groups, beyond, units


def _names(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _nearest_unit(
    folder: str | None,
    unit_of_folder: Mapping[str, str],
    folders: Mapping[str, object],
    memo: dict[str | None, str | None],
) -> str | None:
    """The experiment a folder lies in: itself or the nearest folder above it concluded one."""
    walked: list[str | None] = []
    cursor = folder
    found: str | None = None
    while cursor is not None:
        if cursor in memo:
            found = memo[cursor]
            break
        walked.append(cursor)
        if cursor in unit_of_folder:
            found = unit_of_folder[cursor]
            break
        held = folders.get(cursor)
        cursor = getattr(getattr(held, "entry", None), "parent_entry_id", None)
    for step in walked:
        memo[step] = found
    return found


@declare(
    "experiments_may_belong_together",
    version="4",
    summary="Raise the groups of experiments a deterministic clustering proposes, as questions",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=(
        "common_share",
        "experiment_rules",
        "ignored_theme_kinds",
        "maximum_group_size",
        "maximum_groups",
        "minimum_shared",
        "minimum_similarity",
        "neighbours",
        "policy",
    ),
    support_families=("vocabulary_overlap", "same_parent"),
)
def _build_experiments_may_belong_together() -> Predicate:
    return ExperimentsMayBelongTogether()


class ApprovedExperimentGroup:
    """Files beneath the experiments of a group a person approved, keyed by the group.

    A named rule turns an approved proposal into a claim. The approval starts at the highest level
    where the group appears. Everything under that level inherits approval as grouped if the
    top-level folder is approved as a group. The pass reads which proposed groups a person approved
    from the Record (a ``confirm`` on the proposal's question). It hands each entry the approved
    group whose member experiment's folder lies above it. This predicate returns that group as the
    class key, so every file beneath every member is grouped together. With no approval there is no
    key, and nothing is grouped by the clustering alone.
    """

    name = "approved_experiment_group"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the approved group as the class key, or None where no approved group holds it."""
        del parameters
        group = getattr(subject, "group", None)
        entry = getattr(subject, "entry", None)
        if not group or entry is None:
            return None
        return ClassKey(key=str(group), inputs=(entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no approved group of experiments holds the entry."""
        return Abstention(reason="no group of experiments a person approved holds it")


@declare(
    "approved_experiment_group",
    version="1",
    summary="Group every file beneath the experiments of a group a person approved",
    effect=RuleEffect.GROUPS,
    reads=Reads.APPROVED_GROUP,
    parameters=("proposal_rule",),
    support_families=("inherited_context",),
)
def _build_approved_experiment_group() -> Predicate:
    return ApprovedExperimentGroup()


# ---------------------------------------------------------------------------
# Group resolutions.
# ---------------------------------------------------------------------------

#: The predicates reading what an approved core set of terms resolved. The pass computes the
#: resolutions where a rule in force is on one of them and the Record holds an approved core.
RESOLUTION_PREDICATES = frozenset(
    {
        "resolved_group_within_experiment",
        "resolved_group_across_experiments",
        "group_resolution_deviation",
    }
)

#: What an experiment's terms are and how far a set may deviate, where no rule declares them.
RESOLUTION_DEFAULTS: dict[str, object] = {
    "term_source": "folder_and_acquisitions",
    "acquisition_rules": ["acquisition_stage_from_format"],
    "minimum_word_characters": 2,
    "ignored_theme_kinds": ["stage", "replicate"],
    "minimum_core_terms": 2,
    # A rule written before the lattice declares no mode, so it reads ``each_deviation``: every
    # word added or missing is its own question. The lattice is the default of the rule's version 2.
    "derivations": EACH_DEVIATION,
    "maximum_concepts": DEFAULT_MAXIMUM_CONCEPTS,
    "minimum_experiments": DEFAULT_MINIMUM_EXPERIMENTS,
}

#: Where an experiment's terms are read from.
TERM_SOURCES = ("folder_and_acquisitions", "folder_and_files", "folder_name")

_DIGIT = re.compile(r"\d")


def _left_out(held: EntryRecords, ignored: frozenset[str]) -> set[str]:
    """The components of a name bound to a sense of an ignored kind (a stage, a replicate)."""
    if not ignored:
        return set()
    return {
        binding.component_id
        for binding in held.bindings
        if isinstance(binding, DictionaryBinding)
        and binding.resolution_status is ResolutionStatus.RESOLVED
        and binding.theme_kind in ignored
    }


def _bound_terms(held: EntryRecords) -> dict[str, str]:
    """The Dictionary term each bound word of a name stands for, by component, casefolded.

    A word no sense holds, or two claim level, has none: it stays its spelling
    (:func:`~ingence.grouping.lattice_words.lattice_word`).
    """
    return {
        binding.component_id: binding.term.casefold()
        for binding in held.bindings
        if isinstance(binding, DictionaryBinding)
        and binding.resolution_status is ResolutionStatus.RESOLVED
        and binding.term
    }


def _terms_of(held: EntryRecords) -> dict[str, str]:
    """The terms the lattice reads in place of a name's bound words: none under ``spellings``."""
    return _bound_terms(held) if held.words_as == TERMS else {}


def word_map(entries: Sequence[EntryRecords], *, words_as: str | None = None) -> WordMap:
    """The terms a run's spellings are bound to, for reading a stored answer.

    ``words_as`` defaults to the entries' own (the project's ``groups.words_as``, the same for
    every entry of a run).
    """
    held = list(entries)
    reading = words_as if words_as is not None else (held[0].words_as if held else TERMS)
    pairs: list[tuple[str, str]] = []
    for records in held:
        bound = _bound_terms(records)
        pairs.extend(
            (component.normalized_value, bound[component.component_id])
            for component in records.components
            if component.component_type == "word" and component.component_id in bound
        )
    written = (
        component.normalized_value
        for records in held
        for component in records.components
        if component.component_type == "word"
    )
    return WordMap.from_pairs(pairs, words_as=reading, written=written)


class AnswerReader:
    """A stored ``group:`` answer read in the words the lattice holds.

    Built on the run's entries and made only when an answer is first read, so a run with no
    stored answer pays nothing. Calling it with an answer's words returns each way the lattice
    holds that answer (:meth:`~ingence.grouping.lattice_words.WordMap.answer`): one under
    ``terms``, a few under ``spellings`` where a term is written several ways.
    """

    def __init__(self, entries: Sequence[EntryRecords]) -> None:
        self._entries = entries
        self._map: WordMap | None = None

    def __call__(self, words: Sequence[str]) -> list[tuple[str, ...]]:
        return self.words().answer(words)

    def words(self) -> WordMap:
        """The run's map of spellings to terms, built when first asked for."""
        if self._map is None:
            self._map = word_map(self._entries)
        return self._map


def _words(held: EntryRecords, *, ignored: frozenset[str], minimum: int) -> frozenset[str]:
    """The words a name holds, less a word bound to a sense of an ignored kind.

    A date's or a replicate's word is never a term, whatever the rule declares. Groups are not made
    by dates or replicate numbers. A month's or a weekday's word leaves with the dates and numbers
    that the digit test already leaves out
    (:data:`~ingence.grouping.identifiers.NOT_GROUP_WORD_KINDS`).

    A bound word is its Dictionary term (``groups.words_as``), so two spellings that a person
    called one thing are one word to the lattice. An unbound word stays its spelling.
    """
    left_out = _left_out(held, ignored | NOT_GROUP_WORD_KINDS)
    terms = _terms_of(held)
    return frozenset(
        lattice_word(
            component.normalized_value,
            terms.get(component.component_id),
            words_as=held.words_as,
        )
        for component in held.components
        if component.component_type == "word"
        and len(component.normalized_value) >= minimum
        and component.component_id not in left_out
        # A word holding a digit is a label (a movie's glued number), never a condition.
        and not _DIGIT.search(component.normalized_value)
    )


def experiment_terms(
    entries: Sequence[EntryRecords],
    parameters: Mapping[str, object],
    *,
    units: Mapping[str, str] | None = None,
    folders: Mapping[str, EntryRecords] | None = None,
) -> tuple[dict[str, frozenset[str]], dict[str, str], dict[str, frozenset[str]]]:
    """Each experiment's terms, the experiment each entry lies in, and each file's own words.

    Keyed by entry identifier throughout (:func:`experiment_folders`).

    ``term_source`` ``folder_and_acquisitions`` (the default, flagged) reads the words of the
    experiment folder's own name and of the names of its acquisitions (files a rule in
    ``acquisition_rules`` concluded on: what an instrument wrote). So a new movie with a new word
    changes its experiment's set, and a plain new replicate does not. An analysis tool's output
    vocabulary never enters. Reading every file's words made each core dozens of an analysis
    program's words long and matched nothing, as measured on one laboratory's folder.
    ``folder_and_files`` reads every file's words, and ``folder_name`` the folder's name alone. A
    word shorter than ``minimum_word_characters``, one holding a digit (a movie's glued label), and
    one bound to a sense of an ``ignored_theme_kinds`` kind (a stage or a replicate word: ``Drift``,
    ``rep``) is not a term. Dates and numbers are not words, so two days of one experiment hold
    one set.
    """
    if units is None or folders is None:
        units, folders = experiment_folders(entries, parameters)
    source = str(parameters.get("term_source", RESOLUTION_DEFAULTS["term_source"]))
    if source not in TERM_SOURCES:
        message = f"no term source {source!r}; the sources are {', '.join(TERM_SOURCES)}"
        raise PredicateNotEvaluableError(message)
    ignored = frozenset(str(kind) for kind in _names(parameters.get("ignored_theme_kinds")))
    acquiring = frozenset(_names(parameters.get("acquisition_rules")))
    minimum = int(str(parameters.get("minimum_word_characters", 2)))
    unit_of_folder = {entry_id: entry_id for entry_id in units}
    terms: dict[str, set[str]] = {entry_id: set() for entry_id in units}
    for entry_id in units:
        held = folders.get(entry_id)
        if held is not None:
            terms[entry_id].update(_words(held, ignored=ignored, minimum=minimum))
    placed: dict[str, str] = {}
    words_of: dict[str, frozenset[str]] = {}
    nearest: dict[str | None, str | None] = {}
    for held in entries:
        entry = held.entry
        start = entry.entry_id if entry.entry_type == EntryType.DIRECTORY else entry.parent_entry_id
        key = _nearest_unit(start, unit_of_folder, folders, nearest)
        if key is None:
            continue
        placed[entry.entry_id] = key
        if entry.entry_type in (EntryType.FILE, EntryType.SYMLINK):
            found = _words(held, ignored=ignored, minimum=minimum)
            words_of[entry.entry_id] = found
            if source == "folder_and_files" or (
                source == "folder_and_acquisitions"
                and any(
                    claim.rule_id in acquiring and str(claim.resolution_status) == "resolved"
                    for claim in held.claims
                )
            ):
                terms[key].update(found)
    return {entry_id: frozenset(found) for entry_id, found in terms.items()}, placed, words_of


@dataclass(frozen=True, slots=True)
class ScaledResolution(GroupResolution):
    """What approved sets resolved for one entry, with its series under the lattice.

    A day sits in the group of the most specific approved set it holds (``across``) and in its
    series, the group of the most general (``series``). The two are never merged into one unit.
    They are kept here, beside the only reader, so the inference's own subjects stay as they are.
    """

    series: str | None = None
    #: Under the lattice, on an experiment's folder: its terms as the lattice read them, and every
    #: set it holds that the lattice asks, waits to ask, has had answered, or holds as shared by
    #: too few experiments to ask, each spelled. The clustering leaves a proposal of such a set to
    #: the lattice. Empty under ``each_deviation`` and where the lattice's bound was reached.
    lattice_terms: frozenset[str] = frozenset()
    lattice_sets: frozenset[str] = frozenset()


class Resolutions(dict[str, GroupResolution]):
    """What the approved sets resolved, by entry, and the lattice's bound where it was reached.

    ``lattice_bound`` is ``maximum_concepts`` where the lattice stopped at it (0 otherwise), for
    the run's journal to say so: under the bound the lattice asks nothing.
    """

    lattice_bound: int = 0


def resolution_parameters(
    declared: Mapping[str, object] | None, proposal: Mapping[str, object] | None
) -> dict[str, object]:
    """The parameters the group resolutions read, one definition for the run and the page.

    ``declared`` are the parameters of the resolving rule declaring them (the across rule), and
    ``proposal`` its ``proposal_rule``'s, whose experiments the sets are read over.
    """
    parameters: dict[str, object] = dict(RESOLUTION_DEFAULTS)
    parameters.update(declared or {})
    for name in ("experiment_rules", "policy"):
        if proposal is not None and name in proposal:
            parameters[name] = proposal[name]
    parameters.setdefault(
        "experiment_rules", ["experiment_folder_from_contents", "experiment_folder_from_dated_name"]
    )
    return parameters


def derivation_mode(parameters: Mapping[str, object]) -> str:
    """How approvals spread, ``lattice`` or ``each_deviation``, refusing any other word."""
    mode = str(parameters.get("derivations", RESOLUTION_DEFAULTS["derivations"]))
    if mode not in DERIVATION_MODES:
        message = f"no derivations mode {mode!r}; the modes are {', '.join(DERIVATION_MODES)}"
        raise PredicateNotEvaluableError(message)
    return mode


def _labels(units: Mapping[str, str]) -> dict[str, str]:
    """Each experiment's name in a key a person reads: its path, with its id where two share one."""
    counted: dict[str, int] = {}
    for path in units.values():
        counted[path] = counted.get(path, 0) + 1
    return {
        entry_id: path if counted[path] == 1 else f"{path} [{entry_id}]"
        for entry_id, path in units.items()
    }


def experiment_term_sets_with_never(
    entries: Sequence[EntryRecords], parameters: Mapping[str, object]
) -> tuple[dict[str, frozenset[str]], dict[str, str], dict[str, frozenset[str]]]:
    """Each experiment's terms, its label, and the words its names hold that are not terms."""
    units, folders = experiment_folders(entries, parameters)
    terms, placed, _words = experiment_terms(entries, parameters, units=units, folders=folders)
    return terms, _labels(units), experiment_never_terms(entries, units, folders, placed)


def experiment_term_sets(
    entries: Sequence[EntryRecords], parameters: Mapping[str, object]
) -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """Each experiment's terms and its label: what the lattice is closed over."""
    units, folders = experiment_folders(entries, parameters)
    terms, _placed, _words = experiment_terms(entries, parameters, units=units, folders=folders)
    return terms, _labels(units)


def lattice_of(
    terms: Mapping[str, frozenset[str]],
    parameters: Mapping[str, object],
    *,
    approved: Sequence[Sequence[str]] = (),
    declined: Sequence[Sequence[str]] = (),
    scoped: Sequence[ScopedAnswer] = (),
) -> Lattice:
    """The lattice of these experiments' terms under the rule's parameters, with its states."""
    return group_lattice(
        terms,
        minimum_core_terms=int(str(parameters.get("minimum_core_terms", 2))),
        maximum_concepts=int(str(parameters.get("maximum_concepts", DEFAULT_MAXIMUM_CONCEPTS))),
        approved=approved,
        declined=declined,
        minimum_experiments=int(
            str(parameters.get("minimum_experiments", DEFAULT_MINIMUM_EXPERIMENTS))
        ),
        scoped=scoped,
    )


def _never_words(held: EntryRecords) -> set[str]:
    """The words a name holds bound to a date's or a replicate's sense: not terms."""
    left_out = _left_out(held, NOT_GROUP_WORD_KINDS)
    terms = _terms_of(held)
    return {
        lattice_word(
            component.normalized_value,
            terms.get(component.component_id),
            words_as=held.words_as,
        )
        for component in held.components
        if component.component_id in left_out and component.component_type == "word"
    }


def experiment_never_terms(
    entries: Sequence[EntryRecords],
    units: Mapping[str, str],
    folders: Mapping[str, EntryRecords],
    placed: Mapping[str, str],
) -> dict[str, frozenset[str]]:
    """Each experiment's words that are not terms, from its folder's name and the names beneath."""
    found: dict[str, set[str]] = {entry_id: set() for entry_id in units}
    for entry_id in units:
        held = folders.get(entry_id)
        if held is not None:
            found[entry_id] |= _never_words(held)
    for held in entries:
        key = placed.get(held.entry.entry_id)
        if key is not None:
            found[key] |= _never_words(held)
    return {entry_id: frozenset(words) for entry_id, words in found.items()}


def split_answers(
    sets: Sequence[Sequence[str]], approved: bool, never_of: Mapping[str, frozenset[str]]
) -> tuple[list[tuple[str, ...]], list[ScopedAnswer]]:
    """A person's answers, read in the terms as they are defined.

    An answer naming no word that is not a term stands as it was. An answer that names such a word
    (a month, a replicate's word), given under an earlier version of the rule, is read without it.
    It reaches only the experiments whose own names hold the word. An approval cannot apply to
    something it does not contain, so an experiment nobody was asked about is asked.
    """
    every = frozenset().union(*never_of.values()) if never_of else frozenset()
    plain: list[tuple[str, ...]] = []
    scoped: list[ScopedAnswer] = []
    for found in sets:
        dropped = frozenset(t.casefold() for t in found) & every
        if not dropped:
            plain.append(tuple(found))
            continue
        reduced = tuple(t for t in found if t.casefold() not in dropped)
        if reduced:
            reach = frozenset(e for e, words in never_of.items() if dropped <= words)
            scoped.append(ScopedAnswer(reduced, approved, reach, answered_as=tuple(found)))
    return plain, scoped


def group_resolutions(
    entries: Sequence[EntryRecords],
    cores: Sequence[Sequence[str]],
    parameters: Mapping[str, object],
    *,
    declined: Sequence[Sequence[str]] = (),
) -> dict[str, GroupResolution]:
    """What the approved cores resolve for every entry, by entry identifier.

    Every entry beneath an experiment that shares an approved core is handed the group's key in its
    experiment and across the experiments. The experiment's folder also carries each question it
    raises, with the entries it rests on. Under ``derivations: each_deviation`` those are its
    deviations: its own children leading to the files holding an added word, else the first of
    what it holds. Under ``lattice`` they are the lattice's questions asked of every
    experiment the set holds, raised before any approval too (the most general sets are asked
    first), and an experiment is resolved only by the approved sets it holds. A resolution whose
    questions could not be raised is never admitted: the folder must hold something to cite.
    Children are read by their parent's identifier, from one index built once, so a root that is
    itself an experiment is read like any folder.
    """
    mode = derivation_mode(parameters)
    found = Resolutions()
    if not cores and mode == EACH_DEVIATION:
        return found
    units, folders = experiment_folders(entries, parameters)
    if not units:
        return found
    terms, placed, words_of = experiment_terms(entries, parameters, units=units, folders=folders)
    scoped: list[ScopedAnswer] = []
    if cores or declined:
        never_of = experiment_never_terms(entries, units, folders, placed)
        cores, scoped_cores = split_answers(cores, True, never_of)
        declined, scoped_declined = split_answers(declined, False, never_of)
        scoped = [*scoped_cores, *scoped_declined]
    labels = _labels(units)
    minimum_core_terms = int(str(parameters.get("minimum_core_terms", 2)))
    asked: dict[str, list[str]] = {}
    in_turn: dict[str, set[str]] = {}
    if mode == LATTICE:
        lattice = lattice_of(terms, parameters, approved=cores, declined=declined, scoped=scoped)
        if lattice.bound_reached:
            found.lattice_bound = lattice.maximum_concepts
        for concept in lattice.concepts:
            if concept.state == ASKED:
                for experiment in concept.experiments:
                    if experiment in concept.covered:
                        continue  # an earlier answer reached it: never asked it again
                    asked.setdefault(experiment, []).append(concept.value)
            # A set that too few experiments share: the lattice holds it unasked, and the
            # clustering does not ask it either. By default the lattice asks a set only when five
            # experiments share it (``minimum_experiments``). Once the lattice's bound is reached,
            # no set is left to the lattice, and the clustering proposes groups.
            if not lattice.bound_reached and (
                concept.state != NOT_ASKED or len(concept.experiments) >= FEWEST_EXPERIMENTS
            ):
                for experiment in concept.experiments:
                    in_turn.setdefault(experiment, set()).add(" ".join(concept.terms))
        resolved = resolve_by_lattice(cores, terms, labels=labels, scoped=scoped)
    else:
        resolved = resolve_groups(
            cores, terms, minimum_core_terms=minimum_core_terms, labels=labels
        )
    if not resolved and not asked and not in_turn:
        return found
    children: dict[str, list[str]] = {}
    parent_of: dict[str, str | None] = {}
    for held in entries:
        entry = held.entry
        parent_of[entry.entry_id] = entry.parent_entry_id
        if entry.parent_entry_id is not None:
            children.setdefault(entry.parent_entry_id, []).append(entry.entry_id)

    def child_leading_to(entry_id: str, experiment: str) -> str | None:
        cursor: str | None = entry_id
        while cursor is not None and parent_of.get(cursor) != experiment:
            cursor = parent_of.get(cursor)
        return cursor

    leading: dict[str, dict[str, set[str]]] = {}
    for entry_id, experiment in placed.items():
        resolution = resolved.get(experiment)
        if resolution is None or not resolution.added:
            continue
        for word in sorted(words_of.get(entry_id, frozenset()) & frozenset(resolution.added)):
            child = child_leading_to(entry_id, experiment)
            if child is not None:
                leading.setdefault(experiment, {}).setdefault(word, set()).add(child)
    raised: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}
    for experiment in sorted(set(resolved) | set(asked)):
        held_ids = sorted(children.get(experiment, ()))
        if not held_ids:
            continue  # nothing to cite: its questions could not be raised, so it is not admitted
        deviations: list[tuple[str, tuple[str, ...]]] = []
        resolution = resolved.get(experiment)
        if resolution is not None:
            for word, value in zip(resolution.added, resolution.deviations, strict=False):
                cited = sorted(leading.get(experiment, {}).get(word, set()))[:3] or held_ids[:3]
                deviations.append((value, tuple(cited)))
            for value in resolution.deviations[len(resolution.added) :]:
                deviations.append((value, tuple(held_ids[:3])))
        deviations.extend((value, tuple(held_ids[:3])) for value in asked.get(experiment, ()))
        raised[experiment] = tuple(deviations)
    for entry_id, experiment in placed.items():
        if experiment not in raised:
            continue
        resolution = resolved.get(experiment)
        if resolution is None and entry_id != experiment:
            continue  # asked, not resolved: only the folder carries its questions
        found[entry_id] = ScaledResolution(
            within=resolution.within if resolution is not None else None,
            across=resolution.across if resolution is not None else None,
            deviations=raised[experiment] if entry_id == experiment else (),
            # A series only where it is another group: where the most general approved set is
            # the most specific, the across group says it once.
            series=(
                resolution.series
                if resolution is not None
                and mode == LATTICE
                and resolution.series != resolution.across
                else None
            ),
            lattice_terms=terms.get(experiment, frozenset())
            if entry_id == experiment
            else frozenset(),
            lattice_sets=frozenset(in_turn.get(experiment, ()))
            if entry_id == experiment
            else frozenset(),
        )
    if in_turn:
        # Every experiment's folder carries what the lattice reads, asked of or not, so the
        # clustering can leave the lattice's sets to it.
        for experiment in units:
            if experiment not in found:
                found[experiment] = ScaledResolution(
                    lattice_terms=terms.get(experiment, frozenset()),
                    lattice_sets=frozenset(in_turn.get(experiment, ())),
                )
    return found


class ResolvedGroupWithinExperiment:
    """Files of one experiment sharing an approved core set of terms: each experiment its own."""

    name = "resolved_group_within_experiment"
    #: Version 2: a date's or a replicate's word is not a term, and an approval naming one is read
    #: without it.
    #: Version 3: a bound word is its Dictionary term (``groups.words_as``), and an approval is
    #: read in the words the lattice holds.
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the group's key inside the experiment, or None where no core resolved it."""
        del parameters
        resolution = getattr(subject, "resolution", None)
        entry = getattr(subject, "entry", None)
        key = getattr(resolution, "within", None)
        if not key or entry is None:
            return None
        return ClassKey(key=str(key), inputs=(entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no approved core set of terms resolved the entry's experiment."""
        return Abstention(reason="no core set of terms a person approved resolves it")


#: Which approved set names an experiment's group across the experiments (``scale``): the most
#: specific it holds (its own group, the default) or the most general (its series).
SCALES = ("most_specific", "most_general")


class ResolvedGroupAcrossExperiments:
    """Files of every experiment sharing an approved core set of terms, as one group.

    The group is chosen at a scale. ``most_specific`` keys an experiment by the most specific
    approved set it holds, which is the group that ``each_deviation`` gives and a derivation reads.
    ``most_general`` keys it by the most general, its series across days, which only the lattice
    gives (``each_deviation`` abstains). One rule keys a file once, so each scale is a rule of its
    own.
    """

    name = "resolved_group_across_experiments"
    #: Version 3: a date's or a replicate's word is not a term, and an approval naming one is read
    #: without it.
    #: Version 4: a bound word is its Dictionary term (``groups.words_as``), and an approval is
    #: read in the words the lattice holds.
    version = "4"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the group at the rule's scale of the experiments the set resolved, or None."""
        scale = str(parameters.get("scale", SCALES[0]))
        if scale not in SCALES:
            message = f"no scale {scale!r}; the scales are {', '.join(SCALES)}"
            raise PredicateNotEvaluableError(message)
        resolution = getattr(subject, "resolution", None)
        entry = getattr(subject, "entry", None)
        key = getattr(resolution, "across" if scale == SCALES[0] else "series", None)
        if not key or entry is None:
            return None
        return ClassKey(key=str(key), inputs=(entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no approved core set of terms resolved the entry's experiment."""
        return Abstention(reason="no core set of terms a person approved resolves it")


class GroupDeviation:
    """Each word an experiment adds to, or misses from, an approved core: a question of its own."""

    name = "group_resolution_deviation"
    #: Version 2: a date's or a replicate's word is not a term, and an approval naming one is read
    #: without it.
    #: Version 3: a bound word is its Dictionary term (``groups.words_as``), and an approval is
    #: read in the words the lattice holds.
    version = "3"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise each deviation of the scope's experiment folder, citing what it holds."""
        del parameters
        holder = getattr(subject, "holder", None)
        resolution = getattr(holder, "group_resolution", None)
        deviations = getattr(resolution, "deviations", ())
        if not deviations:
            return None
        values = tuple(value for value, _ in deviations)
        cited = tuple(sorted({entry_id for _, held in deviations for entry_id in held}))
        if all(lattice_terms(value) for value in values):
            # A lattice question: a set of words this experiment shares with others, most
            # general first, then each addition to sets a person approved.
            reason = (
                f"{len(values)} set(s) of words this experiment shares with other experiments, "
                "each asked as a group (an addition asks once what it adds to is approved): "
                + "; ".join(values[:4])
            )
        else:
            reason = (
                f"{len(values)} word(s) this experiment adds to or misses from a group a person "
                "approved: " + "; ".join(values[:4])
            )
        return Raised(values=values, inputs=cited, reason=reason)

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that the folder deviates from no approved core set of terms."""
        return Abstention(reason="the folder deviates from no core set of terms a person approved")


@declare(
    "resolved_group_within_experiment",
    version="3",
    summary="Group the files of one experiment sharing a core set of terms a person approved",
    effect=RuleEffect.GROUPS,
    reads=Reads.APPROVED_GROUP,
    parameters=(),
    support_families=("inherited_context",),
)
def _build_resolved_group_within_experiment() -> Predicate:
    return ResolvedGroupWithinExperiment()


@declare(
    "resolved_group_across_experiments",
    version="4",
    summary="Group the files of every experiment sharing a core set of terms a person approved",
    effect=RuleEffect.GROUPS,
    reads=Reads.APPROVED_GROUP,
    parameters=(
        "acquisition_rules",
        "derivations",
        "ignored_theme_kinds",
        "maximum_concepts",
        "minimum_core_terms",
        "minimum_experiments",
        "minimum_word_characters",
        "proposal_rule",
        "scale",
        "term_source",
    ),
    support_families=("inherited_context", "vocabulary_overlap"),
)
def _build_resolved_group_across_experiments() -> Predicate:
    return ResolvedGroupAcrossExperiments()


@declare(
    "group_resolution_deviation",
    version="3",
    summary="Raise each word an experiment adds to or misses from an approved core, as a question",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=("asked_first",),
    support_families=("vocabulary_overlap",),
)
def _build_group_resolution_deviation() -> Predicate:
    return GroupDeviation()


# ---------------------------------------------------------------------------
# A user's miscellaneous files.
# ---------------------------------------------------------------------------


def miscellaneous_value(path: str) -> str:
    """The question a folder proposed as miscellaneous files raises: its own, one per folder."""
    return f"{path}: miscellaneous files"


class MiscellaneousFiles:
    """The outermost folder of files mostly outside experiments: proposed as miscellaneous.

    Users keep files on their servers that belong to no experiment and to no data collection. The
    rule proposes such a folder as a person's miscellaneous files, for the person to approve. It
    keeps the folder's structure and does not force its files into experiments.

    So, in a pool where some folder is an experiment, a folder is proposed where it neither is nor
    lies in a folder that either experiment rule concluded on (an experiment, acquisitions or an
    analysis), at least ``minimum_files`` of the files beneath it lie outside every experiment,
    they make up at least ``minimum_outside_share`` of its files, and no folder above it is
    proposed too. The proposed folder is the outermost such folder, so a person answers once for
    its whole structure. Miscellany lies beside experiments, so a pool with no experiment is not
    yet read. A root is never proposed. A folder a person already answered about is not asked
    again. The predicate makes a proposal and never a claim. The claim is
    ``miscellaneous_files_approved``'s, from the person's answer.

    Version 2 lets the folder hold an experiment. On one laboratory's folder, a folder of construct
    sequencing held a few dated sequencing runs. Version 1 never asked about a folder that touches
    an experiment, and so it asked about every fragment between them. A named folder that holds
    only its two experiments is offered only with ``organising_folders``. It is off by default,
    because every such folder made dozens of questions on one laboratory's folder. The count
    measures the experiment rules' recall: a data folder that they miss is offered here.

    The pool is read once per run, kept for every folder scope by the pool object's identity.
    """

    name = "miscellaneous_files_to_approve"
    version = "2"

    def __init__(self) -> None:
        self._source: object = None
        self._key: tuple[object, ...] = ()
        self._proposed: frozenset[str] = frozenset()

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Raise the folder's own question where it is proposed, citing what it holds."""
        holder = getattr(subject, "holder", None)
        if holder is None:
            return None
        contents = getattr(holder, "contents", None)
        if getattr(contents, "miscellaneous_answer", None) is not None:
            return None
        if holder.entry.entry_id not in self._proposals(subject, parameters):
            return None
        held = sorted(records.entry.entry_id for records in getattr(subject, "entries", ()))[:3]
        if not held:
            return None
        return Raised(
            values=(miscellaneous_value(holder.entry.relative_path),),
            inputs=tuple(held),
            reason=(
                "no experiment or analysis is this folder, holds it or lies inside it; kept as "
                "it is, for a person to say whether these are their miscellaneous files"
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that the folder is, holds or lies in an experiment, or a folder above is proposed."""
        return Abstention(
            reason="an experiment is, holds or holds this folder, or a folder above it is proposed"
        )

    def _proposals(self, subject: object, parameters: Mapping[str, object]) -> frozenset[str]:
        source = getattr(subject, "pool", None) or getattr(subject, "entries", None) or ()
        key = tuple(sorted((str(k), str(v)) for k, v in parameters.items()))
        if source is self._source and key == self._key:
            return self._proposed
        rules = set(_names(parameters.get("experiment_rules")))
        minimum = int(str(parameters.get("minimum_files", 2)))
        folders: dict[str, EntryRecords] = {}
        files_in: dict[str | None, int] = {}
        experiments: set[str] = set()
        for held in source:
            if not isinstance(held, EntryRecords):
                continue
            entry = held.entry
            if entry.entry_type == EntryType.DIRECTORY:
                folders[entry.entry_id] = held
                if any(
                    claim.rule_id in rules
                    and str(claim.resolution_status) == "resolved"
                    and claim.object_reference is not None
                    for claim in held.claims
                ):
                    experiments.add(entry.relative_path)
            elif entry.entry_type in (EntryType.FILE, EntryType.SYMLINK):
                files_in[entry.parent_entry_id] = files_in.get(entry.parent_entry_id, 0) + 1
        if not experiments:
            # Miscellany is beside experiments: a pool where no rule found an experiment is not
            # yet read, and asking about every folder in it would be noise.
            self._source, self._key, self._proposed = source, key, frozenset()
            return self._proposed
        paths = {held.entry.relative_path: entry_id for entry_id, held in folders.items()}

        def inside(path: str) -> bool:
            """Whether a folder is an experiment or lies in one: a walk up its path."""
            parts = path.split("/")
            return any("/".join(parts[:depth]) in experiments for depth in range(1, len(parts) + 1))

        within = {entry_id: inside(held.entry.relative_path) for entry_id, held in folders.items()}
        # Files beneath each folder, and those of them outside every experiment, deepest first.
        beneath: dict[str, int] = {}
        outside: dict[str, int] = {}
        for entry_id, held in sorted(folders.items(), key=lambda item: -(item[1].entry.depth or 0)):
            here = files_in.get(entry_id, 0)
            beneath[entry_id] = beneath.get(entry_id, 0) + here
            outside[entry_id] = outside.get(entry_id, 0) + (0 if within[entry_id] else here)
            parent = held.entry.parent_entry_id
            if parent in folders:
                beneath[parent] = beneath.get(parent, 0) + beneath[entry_id]
                outside[parent] = outside.get(parent, 0) + outside[entry_id]
        share = float(str(parameters.get("minimum_outside_share", 0.5)))
        candidates = {
            path
            for path, entry_id in paths.items()
            if path
            and (folders[entry_id].entry.depth or 0) > 0
            and not within[entry_id]
            and outside.get(entry_id, 0) >= minimum
            and outside.get(entry_id, 0) >= share * beneath.get(entry_id, 0)
        }
        outermost = {
            path
            for path in candidates
            if not any(
                "/".join(path.split("/")[:depth]) in candidates
                for depth in range(1, path.count("/") + 1)
            )
        }
        if parameters.get("organising_folders") is True:
            # Offering a folder of one's own experiments, such as a named folder that holds two,
            # is off by default. On one laboratory's folder, every folder that directly holds two
            # or more experiments made dozens of questions, year folders among them.
            held_directly: dict[str, int] = {}
            for experiment in experiments:
                parent = experiment.rsplit("/", 1)[0] if "/" in experiment else ""
                held_directly[parent] = held_directly.get(parent, 0) + 1
            needed = int(str(parameters.get("minimum_experiments_held", 2)))
            outermost |= {
                path
                for path, count in held_directly.items()
                if path
                and count >= needed
                and path in paths
                and not within[paths[path]]
                and not any(path == o or path.startswith(o + "/") for o in outermost)
            }
        proposed = frozenset(paths[path] for path in outermost)
        self._source, self._key, self._proposed = source, key, proposed
        return proposed


class ApprovedMiscellaneous:
    """A folder a person kept as their miscellaneous files, claimed so."""

    name = "approved_miscellaneous"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return ``miscellaneous`` where the Record's latest answer on the folder confirms it."""
        del parameters
        if not isinstance(subject, FolderContents) or subject.miscellaneous_answer != "confirm":
            return None
        return Concluded(values=("miscellaneous",), inputs=(subject.entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that nobody has kept the folder as miscellaneous files."""
        return Abstention(reason="nobody has kept this folder as their miscellaneous files")


@declare(
    "miscellaneous_files_to_approve",
    version="2",
    summary="Raise the outermost folder no experiment touches as miscellaneous files to approve",
    effect=RuleEffect.FLAGS,
    reads=Reads.SCOPE,
    parameters=(
        "experiment_rules",
        "minimum_experiments_held",
        "minimum_files",
        "minimum_outside_share",
        "organising_folders",
    ),
    support_families=("filesystem_fact",),
)
def _build_miscellaneous_files() -> Predicate:
    return MiscellaneousFiles()


@declare(
    "approved_miscellaneous",
    version="1",
    summary="Classify a folder a person kept as their miscellaneous files",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.FOLDER_CONTENTS,
    parameters=(),
    support_families=("inherited_context",),
)
def _build_approved_miscellaneous() -> Predicate:
    return ApprovedMiscellaneous()
