"""Predicates for a stage word standing alone in a name, and for one file at two processing steps.

A stage word is read only where it stands alone as a part of the name
(:class:`StageFromNameParts`). The tokeniser cuts ``rawSignal`` into ``raw`` and ``signal``, and
the binder reads ``raw`` as the raw stage, which it is not. The word is inside another word. Also,
``crop.raw.bam`` names a step a tool did after the first dot, where a type chain is written
(``.srt.bam``, ``.ome.tif``). So the predicate reads the entry beside its bindings and keeps a
binding only where the word it bound is one whole piece of the name's head, cut on ``_``, ``-`` and
a space, before the first dot. An archive bundle is not at a stage: ``..._RAW.tar`` is named for
what it holds, and a file whose extension bound to an archive format is left unstaged by its name.

One file at two processing steps is not a repeat (:class:`LaterStepOfSameFile`). ``X.bam`` and
``X.aligned.bam``, or ``X_raw.nd2`` and ``X_processed.nd2``, are one file at two steps. A key that
left stage words out would group them as replicates of one condition, so the replicates rule counts
stage words. The class ``same_file_at_two_steps`` leaves them out, and this module reads that class
as derivation candidates. The name carrying the fewer stage words, or the earlier stage in the order
the rule states, is proposed as the source of the one carrying more, link by link.
"""

from __future__ import annotations

import fnmatch
import re
from typing import TYPE_CHECKING

from ingence.core.schema import (
    DictionaryBinding,
    FailureClass,
    MatchQuality,
    RuleEffect,
    SupportComponent,
    match_quality_meets,
)
from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.predicates.classify import (
    _floor,
    _named_senses,
    _names,
    _on_axis,
    _stands_between,
)
from ingence.rulebook.registry import (
    Abstention,
    Concluded,
    PairsWithinTheClass,
    Predicate,
    Reads,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.core.schema import Entry
    from ingence.infer.subjects import EntryRecords

__all__ = [
    "NEIGHBOUR_PREDICATES",
    "LaterStepOfASibling",
    "LaterStepOfSameFile",
    "StageFromNameParts",
    "StageInWorkflow",
    "standing_parts",
]

#: The predicates that read what a file's folder holds beside it, which the pass attaches only
#: where one is in force (as a rule reading proximity has it attached).
NEIGHBOUR_PREDICATES: frozenset[str] = frozenset({"later_step_of_a_sibling"})

#: What separates the pieces of a name: an underscore, a dash, a space, brackets, a comma,
#: a plus, an ampersand.
_SEPARATORS = re.compile(r"[\s_\-()\[\],+&]+")
#: A dot between two digits is part of a number, a version or a dotted date, never a cut.
_DOT_IN_A_NUMBER = re.compile(r"(?<=\d)\.(?=\d)")
_KEPT_DOT = "\x00"


def standing_parts(name: str, extension: str | None) -> frozenset[str]:
    """The pieces of a name that stand alone, casefolded: the stem (the extension taken off),
    cut on the separators and on a dot, but for a dot between two digits (``2.5mM``, ``v1.2``,
    ``01.03.2024``); and the dotted piece that directly adjoins the extension is a tool's suffix
    (``crop.raw.bam``), never where the file sits.

    ``kelt_raw.nd2`` gives ``kelt`` and ``raw``; ``kelt (raw).tif`` the same; ``rawSignal.tif``
    gives ``rawsignal`` (so the stage word inside it is not a part); ``crop.raw.bam`` gives
    ``crop``; ``raw.kelt.tif`` gives ``raw``; ``kelt.raw`` with no extension gives both.
    """
    stem = name
    if extension:
        suffix = "." + extension.casefold()
        if stem.casefold().endswith(suffix) and len(stem) > len(suffix):
            stem = stem[: -len(suffix)]
    pieces = [piece for piece in _SEPARATORS.split(stem) if piece]
    parts: list[str] = []
    for position, piece in enumerate(pieces):
        guarded = _DOT_IN_A_NUMBER.sub(_KEPT_DOT, piece)
        dotted = [held.replace(_KEPT_DOT, ".") for held in guarded.split(".") if held]
        if extension and position == len(pieces) - 1 and len(dotted) > 1:
            dotted = dotted[:-1]
        parts.extend(dotted)
    return frozenset(part.casefold() for part in parts)


def _stem_extension(entry: Entry) -> str | None:
    return entry.compound_extension or entry.extension


#: The slot whose bindings count a file's own extension as a name token, which the engine refuses.
_EXTENSION_SLOT = "extension"


def _free(bindings: Sequence[DictionaryBinding]) -> list[DictionaryBinding]:
    """The bindings the extension slot did not produce.

    Every name-slot binding is kept, whatever marker it carries. A word that two domains hold
    differently binds the universal sense under ``name:universal_sense``, and a number binds under
    ``name:exact``. Both are still the name's own words. Only a binding of the extension slot
    (``extension:...``) counts the file's extension as a token.
    """
    return [
        binding
        for binding in bindings
        if binding.constraint is None or binding.constraint.split(":", 1)[0] != _EXTENSION_SLOT
    ]


class StageFromNameParts:
    """What a name's free words mean on one axis, counted only where a word stands alone.

    This is the same reading as ``sense_from_name_tokens``: one hit per distinct sense, a conflict
    on the axis is the rule unable to decide, and the extension slot's bindings are left out. It
    adds two refusals, each a parameter. ``whole_parts_only`` keeps a binding only where the word it
    bound is one whole piece of the name's head (:func:`standing_parts`). ``not_in_formats`` leaves
    a file unstaged by its name where its extension bound to one of the formats named (an archive
    bundle). The predicate reads the entry's records whole, since the name's own spelling is what
    the first refusal compares against.
    """

    name = "stage_from_name_parts"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the distinct senses the standing parts of the name bound to, or nothing."""
        records = _records(subject)
        if records is None:
            return ()
        if _bundled(records, parameters):
            return ()
        found = self._found(records, parameters)
        minimum = max(1, int(str(parameters.get("minimum_senses", 1))))
        if len(found) < minimum:
            return ()
        return Concluded(
            values=tuple(sorted(found)),
            inputs=tuple(sorted(binding_id for held in found.values() for binding_id in held)),
        )

    def _found(
        self, records: EntryRecords, parameters: Mapping[str, object]
    ) -> dict[str, list[str]]:
        required_kind = str(parameters.get("theme_kind", ""))
        named = _named_senses(parameters)
        floor = _floor(parameters)
        whole = _whole_parts_only(parameters)
        parts = standing_parts(records.entry.name, _stem_extension(records.entry))
        values = {
            component.component_id: component.normalized_value.casefold()
            for component in records.components
        }
        found: dict[str, list[str]] = {}
        for binding in _free(records.bindings):
            if (
                binding.resolution_status is not ResolutionStatus.RESOLVED
                or binding.theme_kind != required_kind
                or binding.term is None
                or (named is not None and binding.term not in named)
                or not match_quality_meets(binding.quality, floor)
            ):
                continue
            if whole and values.get(binding.component_id, "") not in parts:
                continue
            found.setdefault(binding.term, []).append(binding.binding_id)
        return found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why no stage was concluded: a bundle, a word inside a word, a conflict, nothing."""
        records = _records(subject)
        if records is None:
            return Abstention(reason="the rule reads an entry's records and was handed none")
        required_kind = str(parameters.get("theme_kind", ""))
        if _bundled(records, parameters):
            return Abstention(
                inputs=tuple(sorted(b.binding_id for b in records.bindings if b.constraint)),
                reason="the file is an archive bundle, named for what it holds and not for a stage",
            )
        named = _named_senses(parameters)
        free = _free(records.bindings)
        conflicting = tuple(
            sorted(
                binding.binding_id
                for binding in free
                if binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
                and (
                    _on_axis(binding, required_kind)
                    if named is None
                    else _stands_between(binding, named, theme_kind=required_kind)
                )
            )
        )
        if conflicting:
            return Abstention(
                status=ResolutionStatus.EXAMINED_CONFLICTING,
                failure_class=FailureClass.INTERPRETATION,
                inputs=conflicting,
                reason=f"a name token stood between senses of kind {required_kind}",
            )
        found = self._found(records, parameters)
        if found:
            minimum = max(1, int(str(parameters.get("minimum_senses", 1))))
            return Abstention(
                inputs=tuple(sorted(i for held in found.values() for i in held)),
                reason=f"{len(found)} distinct senses of kind {required_kind}, {minimum} asked for",
            )
        bound = [
            binding
            for binding in free
            if binding.resolution_status is ResolutionStatus.RESOLVED
            and binding.theme_kind == required_kind
            and binding.term is not None
            and (named is None or binding.term in named)
        ]
        if bound and _whole_parts_only(parameters):
            return Abstention(
                inputs=tuple(sorted(binding.binding_id for binding in bound)),
                reason=(
                    "the stage word is written inside another word, or after the first dot "
                    "where a type chain is written, so it names a step done and not a stage"
                ),
            )
        return Abstention(
            reason=f"no standing part of the name bound to a sense of kind {required_kind}"
        )


def _records(subject: object) -> EntryRecords | None:
    entry = getattr(subject, "entry", None)
    bindings = getattr(subject, "bindings", None)
    if entry is None or bindings is None or not hasattr(subject, "components"):
        return None
    return subject  # type: ignore[return-value]


def _whole_parts_only(parameters: Mapping[str, object]) -> bool:
    value = parameters.get("whole_parts_only", True)
    return value if isinstance(value, bool) else str(value).casefold() in {"true", "yes"}


def _bundled(records: EntryRecords, parameters: Mapping[str, object]) -> bool:
    """Whether the entry's extension bound to a format the rule leaves unstaged (an archive)."""
    refused = _names(parameters, "not_in_formats")
    if not refused:
        return False
    return any(
        binding.constraint is not None
        and binding.resolution_status is ResolutionStatus.RESOLVED
        and binding.term in refused
        for binding in records.bindings
    )


# ---------------------------------------------------------------------------
# One file at two processing steps.
# ---------------------------------------------------------------------------


def _stage_words(records: EntryRecords, kind: str, floor: MatchQuality) -> frozenset[str]:
    """The stage senses a file's free words bound to, at the floor or better."""
    return frozenset(
        binding.term
        for binding in _free(records.bindings)
        if binding.resolution_status is ResolutionStatus.RESOLVED
        and binding.theme_kind == kind
        and binding.term is not None
        and match_quality_meets(binding.quality, floor)
    )


def _rank(stages: frozenset[str], order: Sequence[str]) -> int | None:
    """The furthest place in ``order`` a file's stage words reach; None where none is in it."""
    places = [order.index(stage) for stage in stages if stage in order]
    return max(places) if places else None


class LaterStepOfSameFile(PairsWithinTheClass):
    """Within a class of names agreeing but for stage words, the earlier step is the source.

    Two readings order a pair, and nothing else does. In the first, the source's stage words are a
    strict subset of the target's (``X.bam`` before ``X.aligned.bam``: the later step keeps the name
    and adds what was done). In the second, both name stages and the target's furthest stage comes
    later in ``stage_order`` (``X_raw.nd2`` before ``X_processed.nd2``). Two names reaching the same
    place are not ordered. A chain is proposed link by link: a pair is never formed where a third
    member of the class stands between its ends on either reading. The predicate reads no time. A
    copy's clock is a copy's, and the name is the evidence here.
    """

    name = "later_step_of_same_file"
    version = "1"

    def __init__(self) -> None:
        self._last: tuple[Sequence[object], dict[str, tuple[str, ...]]] | None = None

    def pairs_within(
        self, key: str, members: Sequence[object], parameters: Mapping[str, object]
    ) -> Mapping[str, tuple[str, ...]]:
        """Each earlier step's later steps, nearest first, by identifier."""
        del key
        last = self._last
        if last is not None and last[0] is members:
            return last[1]
        kind = str(parameters.get("theme_kind", "stage"))
        floor = _floor(parameters)
        order = [str(stage) for stage in _ordered(parameters)]
        held: dict[str, frozenset[str]] = {}
        for member in members:
            records = _records(member)
            if records is not None:
                held[records.entry.entry_id] = _stage_words(records, kind, floor)
        found: dict[str, tuple[str, ...]] = {}
        for source, before in held.items():
            targets = [
                target
                for target, after in held.items()
                if target != source and _earlier(before, after, order)
            ]
            # Link by link: a target another target stands before is that target's.
            nearest = tuple(
                sorted(
                    target
                    for target in targets
                    if not any(
                        other != target and _earlier(held[other], held[target], order)
                        for other in targets
                    )
                )
            )
            if nearest:
                found[source] = nearest
        self._last = (members, found)
        return found

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the target being the source at a later step, or None."""
        found = self._read(subject, parameters)
        return None if isinstance(found, str) else found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why the two are not one file at two steps."""
        found = self._read(subject, parameters)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _read(
        self, subject: object, parameters: Mapping[str, object]
    ) -> tuple[SupportComponent, ...] | str:
        members = getattr(subject, "members", ())
        if not members:
            return "the pair was not drawn from a class, so no name agrees with another"
        pairs = self.pairs_within("", members, parameters)
        source = getattr(getattr(subject, "source", None), "entry", None)
        target = getattr(getattr(subject, "target", None), "entry", None)
        if source is None or target is None:
            return "the pair has no two ends"
        if target.entry_id not in pairs.get(source.entry_id, ()):
            return (
                "the names reach the same stage, or the target is not the next step after the "
                "source in its class"
            )
        cited = tuple(
            sorted(
                {
                    str(getattr(subject, "source_membership", "")),
                    str(getattr(subject, "target_membership", "")),
                }
                - {""}
            )
        )
        return (
            SupportComponent(
                name="same_file_at_a_later_step",
                family="name_binding",
                value=1.0,
                observations=cited,
            ),
        )


def _ordered(parameters: Mapping[str, object]) -> Sequence[object]:
    value = parameters.get("stage_order", ())
    return value if isinstance(value, (list, tuple)) else ()


def _earlier(before: frozenset[str], after: frozenset[str], order: Sequence[str]) -> bool:
    """Whether ``before`` names an earlier step than ``after``, by either reading."""
    if before < after:
        return True
    if not before or not after or before == after:
        return False
    first, second = _rank(before, order), _rank(after, order)
    return first is not None and second is not None and first < second


# ---------------------------------------------------------------------------
# A file that is the later step of a sibling.
# ---------------------------------------------------------------------------

#: What a stage word is joined to its name by, in a sibling key.
_JOINERS = "._\\- "


class LaterStepOfASibling:
    """A file whose name is a sibling's name with a stage word added.

    Examples are ``X.aligned.bam`` beside ``X.bam``, and ``kelt_001_filtered.csv`` beside
    ``kelt_001.csv``. A tool kept the name and added what it did, so the file is the sibling at a
    later step and not a replicate of it. The rule reads the file's own bindings (which of its words
    are stage words) and what its folder holds beside it (the names of the files holding bytes, as
    the pass attaches them, numbers and extension kept). The file's own name with each stage word
    and its joiner taken out is a sibling's name, and the rule fires. Numbers and the extension
    count, as measured on the sample collection: ``..._R2_analysis.csv`` beside ``..._R3.csv`` is
    another replicate and not the same file, and ``X_proc.tif`` beside ``X.nd2`` is a derivation
    into another format, and this rule does not fire on it. The rule reads nothing of a neighbour's
    records, only the names.

    Two later steps beside each other (``R1.aligned.bam``, ``R2.aligned.bam``) both fire where
    ``R1.bam`` and ``R2.bam`` lie beside them, and are then not replicates of each other either.
    This is a known loss, stated in the replicates rule's rationale.
    """

    name = "later_step_of_a_sibling"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return ``later_step`` where a sibling's key is this file's less its stage words."""
        records = _records(subject)
        if records is None:
            return ()
        sibling = _earlier_sibling(records, parameters)
        if sibling is None:
            return ()
        _key, bindings = sibling
        return Concluded(values=("later_step",), inputs=tuple(sorted(bindings)))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that no sibling is this file at an earlier step."""
        del parameters
        return Abstention(reason="no file beside it has this file's name less a stage word")


def _earlier_sibling(
    records: EntryRecords, parameters: Mapping[str, object]
) -> tuple[str, list[str]] | None:
    """The sibling name this file's name reduces to, less its stage words, and the bindings."""
    kind = str(parameters.get("theme_kind", "stage"))
    floor = _floor(parameters)
    values = {c.component_id: c.normalized_value.casefold() for c in records.components}
    words: dict[str, list[str]] = {}
    for binding in _free(records.bindings):
        if (
            binding.resolution_status is ResolutionStatus.RESOLVED
            and binding.theme_kind == kind
            and binding.term is not None
            and match_quality_meets(binding.quality, floor)
        ):
            word = values.get(binding.component_id, "")
            if word:
                words.setdefault(word, []).append(binding.binding_id)
    if not words:
        return None
    beside: frozenset[str] = getattr(records, "beside_names", frozenset())
    if not beside:
        return None
    own = records.entry.name.casefold()
    reduced = own
    for word in sorted(words, key=len, reverse=True):
        pattern = re.compile(
            rf"(?:[{_JOINERS}]{re.escape(word)}(?=$|[{_JOINERS}])|^{re.escape(word)}[{_JOINERS}])"
        )
        reduced = pattern.sub("", reduced)
    if not reduced or reduced == own or reduced not in beside:
        return None
    return reduced, [binding_id for held in words.values() for binding_id in held]


# ---------------------------------------------------------------------------
# A file's stage from the workflow in force.
# ---------------------------------------------------------------------------


class StageInWorkflow:
    """A file's stage from its type and its folder within the workflow in force.

    The rule is read after the suggester and never among the first phase's classifying rules. The
    workflows a subject carries are the ones a person declared and the ones the pass suggested from
    the earlier phases' results. So the pass hands them over after the suggester (``infer/run.py``),
    where a classifying rule reading them cannot be among the rules the suggester read. Each
    workflow is read on its own. The stages whose file types hold the file's extension (``by_type``)
    are the candidates. Where two do, the folder the file lies in decides between them
    (``by_folder``: its name among a stage's folders, as a word or a glob). A stage listing the
    file's name among its name patterns is taken whatever the type says. One stage left is the
    conclusion, resting on the entry itself (the workflow is a decision, not a record). Two
    workflows that place the file differently place it nowhere, as a file at two rungs has no
    logical context. A file whose name matches a workflow's exempt pattern is left alone by that
    workflow. A file that a rule reading the file itself placed is left as placed: the workflow sits
    below the format rule and the name rules, and places what they did not. Measured on the sample
    collection, placing by type alone read a raw image saved as a plain TIFF as processed, against
    its own name.

    A suggestion is read only inside the experiments it was counted from, at the rungs each of them
    reached, or inside a folder one of its stages lists (:func:`_within`). A file outside them gave
    no evidence for the suggestion and is not placed by it. A folder a stage lists is never
    contradicted. A bundle (``not_in_formats``) is left alone, as a noise file and a placeholder
    are through the rule's defeaters.
    """

    name = "stage_in_workflow"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the one stage the workflows in force place the file at, or nothing."""
        records = _records(subject)
        if records is None or _staged_already(records) or _bundled(records, parameters):
            return ()
        placed = self._placed(records, parameters)
        if len(placed) != 1:
            return ()
        return Concluded(values=(next(iter(placed)),), inputs=(records.entry.entry_id,))

    def _placed(self, records: EntryRecords, parameters: Mapping[str, object]) -> set[str]:
        by_type = _flag(parameters, "by_type", default=True)
        by_folder = _flag(parameters, "by_folder", default=True)
        entry = records.entry
        extension = (entry.compound_extension or entry.extension or "").casefold()
        bare = (entry.extension or "").casefold()
        folder = entry.relative_path.split("/")[-2].casefold() if "/" in entry.relative_path else ""
        name = entry.name.casefold()
        placed: set[str] = set()
        place = getattr(records, "experiment_place", None)
        experiment = getattr(place, "experiment", None)
        for workflow in getattr(records, "workflows", ()):
            if not getattr(workflow, "stages", ()):
                continue
            if any(fnmatch.fnmatchcase(name, pattern.casefold()) for pattern in workflow.exempt):
                continue
            reached = _within(workflow, entry.relative_path, experiment, folder)
            if reached is None:
                continue
            named = [
                stage.stage
                for stage in workflow.stages
                if any(fnmatch.fnmatchcase(name, pattern.casefold()) for pattern in stage.names)
            ]
            if len(named) == 1:
                placed.add(named[0])
                continue
            found = (
                [
                    stage.stage
                    for stage in workflow.stages
                    if extension and (extension in stage.types or bare in stage.types)
                ]
                if by_type
                else []
            )
            # A suggestion fills only the rungs the file's own experiment reached.
            if reached:
                found = [stage for stage in found if stage in reached]
            if by_folder and folder:
                # The folder a stage lists decides, and is never contradicted: a file in a
                # folder listed at one stage is placed at no other.
                listed = [
                    stage.stage
                    for stage in workflow.stages
                    if any(fnmatch.fnmatchcase(folder, held.casefold()) for held in stage.folders)
                ]
                if listed:
                    found = [stage for stage in found if stage in listed]
            if len(found) == 1:
                placed.add(found[0])
        return placed

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why no workflow placed the file, or that two placed it differently."""
        records = _records(subject)
        if records is None:
            return Abstention(reason="the rule reads an entry's records and was handed none")
        if _staged_already(records):
            return Abstention(
                inputs=tuple(sorted(c.claim_id for c in records.claims if _is_stage_claim(c))),
                reason="a rule reading the file itself placed it at a stage, which stands",
            )
        if _bundled(records, parameters):
            return Abstention(
                inputs=tuple(sorted(b.binding_id for b in records.bindings if b.constraint)),
                reason="the file is an archive bundle, named for what it holds and not for a stage",
            )
        workflows = tuple(getattr(records, "workflows", ()))
        if not any(getattr(workflow, "stages", ()) for workflow in workflows):
            return Abstention(
                reason="no workflow in force names the file types or folders of its stages"
            )
        placed = self._placed(records, parameters)
        if len(placed) > 1:
            return Abstention(
                inputs=(records.entry.entry_id,),
                reason=f"two workflows in force place the file at different stages: "
                f"{', '.join(sorted(placed))}",
            )
        place = getattr(records, "experiment_place", None)
        experiment = getattr(place, "experiment", None)
        folder = (
            records.entry.relative_path.split("/")[-2].casefold()
            if "/" in records.entry.relative_path
            else ""
        )
        if all(
            _within(workflow, records.entry.relative_path, experiment, folder) is None
            for workflow in workflows
        ):
            return Abstention(
                reason=(
                    "the file lies in no experiment a suggested workflow was counted from, in "
                    "none of its stages' folders, and outside every declared workflow's scope"
                )
            )
        return Abstention(
            reason="no stage of a workflow in force lists the file's type, folder or name"
        )


def _within(
    workflow: object, relative_path: str, experiment: str | None, folder: str
) -> frozenset[str] | None:
    """Whether a workflow applies to a file, and which of its rungs the file may be placed at.

    The result is None where the workflow does not apply. A suggestion applies only inside an
    experiment it was counted from, never project-wide, and then only at the rungs that
    experiment's own chain reached. So a type listed at a rung from other experiments' files never
    fills a rung this one never had. A suggestion also applies inside a folder one of its stages
    lists, at that stage. A declared workflow applies within its scope, a root or folder prefix, or
    everywhere where it names none, at every rung (an empty set).
    """
    scope = getattr(workflow, "scope", None)
    if isinstance(scope, str) and scope:
        prefix = scope.strip("/").casefold()
        held = relative_path.casefold()
        if held != prefix and not held.startswith(prefix + "/"):
            return None
    if getattr(workflow, "origin", "declared") != "suggested":
        return frozenset()
    followed = dict(getattr(getattr(workflow, "evidence", None), "followed", ()))
    if experiment is not None and experiment in followed:
        return frozenset(followed[experiment])
    listed = frozenset(
        stage.stage
        for stage in getattr(workflow, "stages", ())
        if folder and any(fnmatch.fnmatchcase(folder, held.casefold()) for held in stage.folders)
    )
    return listed or None


def _is_stage_claim(claim: object) -> bool:
    predicate = str(getattr(claim, "predicate", ""))
    return (
        predicate.startswith("classifies.")
        and "stage" in predicate
        and str(getattr(claim, "resolution_status", "")) == "resolved"
    )


def _staged_already(records: EntryRecords) -> bool:
    """Whether a rule reading the file itself (its format, its name) already placed it: the
    workflow places what nothing else did, and never contradicts a reading of the file."""
    return any(_is_stage_claim(claim) for claim in records.claims)


def _flag(parameters: Mapping[str, object], name: str, *, default: bool) -> bool:
    value = parameters.get(name, default)
    return value if isinstance(value, bool) else str(value).casefold() in {"true", "yes"}


@declare(
    "later_step_of_a_sibling",
    version="1",
    summary="Classify a file as a sibling at a later step, by its name less its stage words",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.RECORDS,
    parameters=("minimum_match_quality", "theme_kind"),
    support_families=("name_binding",),
)
def _build_later_step_of_a_sibling() -> Predicate:
    return LaterStepOfASibling()


@declare(
    "stage_in_workflow",
    version="2",
    summary="Classify a file by the stage of the workflow in force its type and folder put it at",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.RECORDS,
    parameters=("by_folder", "by_type", "not_in_formats"),
    support_families=("name_binding",),
)
def _build_stage_in_workflow() -> Predicate:
    return StageInWorkflow()


@declare(
    "stage_from_name_parts",
    version="1",
    summary="Classify a file by the stage a standing part of its name states",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.RECORDS,
    parameters=(
        "minimum_match_quality",
        "minimum_senses",
        "not_in_formats",
        "senses",
        "theme_kind",
        "whole_parts_only",
    ),
    support_families=("name_binding",),
)
def _build_stage_from_name_parts() -> Predicate:
    return StageFromNameParts()


@declare(
    "later_step_of_same_file",
    version="1",
    summary="Propose one file at a later processing step as made from itself at the earlier",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("minimum_match_quality", "stage_order", "theme_kind"),
    support_families=("name_binding",),
    reach="pair",
)
def _build_later_step_of_same_file() -> Predicate:
    return LaterStepOfSameFile()
