"""Predicates that propose candidate edges.

A proposing rule contributes positive support for an edge. What it is allowed to score on is the
question that decides whether the engine's conclusions mean anything. Two unrelated projects that
run in parallel overlap perfectly, and every laboratory has months where three unconnected efforts
were active. So a burst or a window may bound which candidates are generated and may be displayed,
and may never make a proposal more credible.

The prohibition is enforced at load time from the families declared here, in
:mod:`ingence.rulebook.load`, and is not left to review. It is easy to violate, because a timeline
makes co-occurrence look like evidence.

Support is a vector and never a sum, which is why every registration below declares families and
not weights. Components that share a family are views of one fact. A shared stem, a shared
normalized family and a fuzzy string similarity are three readings of the same name and must not
compound with each other. An engine that summed named components into one number and clamped it
into the unit interval would make a hard conflict worth exactly as much as a pile of weak
agreement could offset. Keeping the vector whole is what lets a conflict be a refusal and not a
large negative weight.

Nothing here generates candidates. Each of these predicates scores a pair it is handed. Which
pairs exist at all is bounded candidate generation, which lives in ``infer/`` and must never be
all-pairs.

Derivations come before stages. A derivation is found from its own evidence and takes its
direction from it: a file named for an acquisition is made from it, and a script's calls read one
file and write the other. A name identity and a describing file that names both give no direction.
So the earliest credible time orders the pair, and with no time the pair is proposed undirected,
as ``concerns``, by a rule of its own. A stage step corroborates a derivation found by evidence
and never creates one (:class:`DerivesFromStageTransition` proposes nothing). A clock orders a
pair and supports nothing: its component weighs zero.
"""

from __future__ import annotations

import bisect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.schema import Claim, RuleEffect, SupportComponent
from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.predicates.series import (
    CLOCK_TOLERANCE_SECONDS,
    SeriesReading,
    counting_kinds,
    read_series,
)
from ingence.rulebook.registry import (
    CO_MENTION_INDEX,
    MENTION_INDEX,
    Abstention,
    DrawsFromTheClassKey,
    PairsWithinTheClass,
    Predicate,
    PredicateNotEvaluableError,
    RanksPairEnds,
    Reads,
    declare,
)

if TYPE_CHECKING:
    from ingence.infer.references import Resolution
    from ingence.infer.subjects import EntryRecords

__all__ = [
    "ConcernsByFoundSense",
    "DerivesFromAggregation",
    "DerivesFromDeclaredReference",
    "DerivesFromNameFamily",
    "DerivesFromNamedAcquisition",
    "DerivesFromScriptCalls",
    "DerivesFromStageTransition",
    "DerivesIntoASeriesSummary",
    "DerivesWithinANameIdentity",
    "DerivesWithinOneNameBeside",
    "EquivalentByMultiSignalDesign",
    "ReferencesByDeclaredPath",
    "SucceedsByOrderedToken",
]


class DerivesFromNameFamily:
    """Propose that a derived file came from a source sharing its name family.

    This is the commonest real provenance signal in a laboratory filesystem: a projection named
    after an acquisition, with a marker prefixed or a suffix swapped. The shared family is what
    says so.

    The rule scores on the name and on the shape, and on nothing temporal. The loader checks the
    families declared at registration, and the omission of ``burst`` and ``window`` from them is
    the constraint and not an oversight. A same-folder term is present and weak. In a workflow
    bundle the same folder means the same experiment, and in a type silo it means the same assay
    across many experiments. The regime supplies that meaning.

    Declared and not evaluable. It scores a pair drawn from one name family. The proposing phase
    draws such pairs within a declared index, and what is missing is this predicate's own scoring
    of a pair's names and shapes. A name family is not a component that the census produces. It is
    the key that ``same_name_family`` computes from a name's word components.
    """

    name = "derives_from_name_family"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "derives_from_name_family scores a candidate pair on its names and shapes, "
            "and scoring a pair that way is not implemented"
        )
        raise PredicateNotEvaluableError(message)


class DerivesFromDeclaredReference:
    """Propose a derivation where something written down names both ends.

    Examples are a script that reads one path and writes another, a log that records what a run
    consumed and produced, and a notebook whose cell names its input file. This is the only
    provenance evidence in the engine that is testimony and not inference. It is worth more than
    everything else here put together. Every other proposing rule reconstructs what probably
    happened from what the files look like. This one reads an account of what did happen, written
    at the time by the thing that did it.

    Testimony is not proof, and the tier says so. A script states its intent and not its outcome.
    The path may be a template, the run may have failed, and the output may have been overwritten
    by a later one. So a declared reference proposes at the strongest decision tier the engine has
    and still proposes and does not assert.

    A reference is not a derivation, and conflating them is a trap. A file that mentions two paths
    has referenced them, which is its own primitive and the only one that permits cycles. Turning
    that into a derivation requires the referring file to be a process description and the two
    paths to sit on opposite sides of it. A configuration file that lists twenty inputs derives
    nothing from any of them, and a rule that read co-mention as provenance would connect all
    twenty to each other.

    The rule is evaluable and deliberately narrow. A pair is drawn within ``co_mentioned``: two
    entries that one file's mentions resolve to, with that file handed over as ``describing``.
    Three things must hold, and each is a separate abstention:

    1. The describing file describes a process. It carries a resolved classification whose value
       is one of ``describing_formats`` (a script's or a workspace's file type) or
       ``describing_roles`` (a protocol, a note). A configuration file's twenty inputs stay twenty
       references.
    2. Both mentions resolved at the rule's floor (``minimum_resolution``, the reference rule's
       scale). A name matched only by its last component places a pair and licenses nothing.
    3. The pair has a direction, or none (version 5). A mention says a file was named and never
       whether it was read or written, so the describing file gives the pair no direction of its
       own. The earliest credible time orders it (:func:`_order`). Where no time does, a rule with
       ``direction: undirected`` proposes it once as related. The shipped book orders no describing
       file's pair by time (``ordered_by_time: false``). On an invented test laboratory, every pair
       that the time ordered was two inputs of one script, acquired in turn and made from nothing
       in the pair. Version 4 took the direction from a stage step, so two files that name no
       stage were never proposed. A stage step corroborates and never decides
       (``stage_transitions``, a component of its own where the step agrees).

    ``maximum_separation`` is declared by the shipped rule and read by nothing. How many described
    steps separate two files is a reading of a script's structure, which no extractor makes.
    """

    name = "derives_from_declared_reference"
    version = "6"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the pair, directed or undirected as the rule asks, or None."""
        found = self._read(subject, parameters)
        return None if isinstance(found, str) else found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say which of the three things did not hold."""
        found = self._read(subject, parameters)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _read(
        self, subject: object, parameters: Mapping[str, object]
    ) -> tuple[SupportComponent, ...] | str:
        cheap = _refused_before_time(subject, parameters)
        if cheap is not None:
            return cheap
        describing = getattr(subject, "describing", None)
        if getattr(subject, "within", None) != CO_MENTION_INDEX or describing is None:
            return "the pair was not drawn from one file's mentions"
        if _called_both_ways(subject):
            return (
                "the script's own calls read one end and write the other, and the rule reading "
                "its calls proposes the pair"
            )
        described = _describes(describing, parameters)
        if described is None:
            return "the file mentioning both is not of a kind that describes a process"
        if not _both_ends_resolved(subject, parameters):
            return (
                "an end was named only below the rule's minimum_resolution, which a derivation "
                "needs as much as a reference does"
            )
        directed = _directed_by(subject, parameters, _order(subject, parameters))
        if isinstance(directed, str):
            return directed
        mentions = tuple(
            sorted(
                {resolution.mention.observation_id for resolution in _undirected(subject)}
                | {described}
            )
        )
        return (
            SupportComponent(
                name="declared_reference",
                family="declared_reference",
                value=1.0,
                observations=mentions,
            ),
            *directed,
        )


class DerivesFromScriptCalls:
    """Propose a derivation where one script reads one file and writes the other.

    The direction is the script's own. A call that reads ``kelt_counts.csv`` and a call that writes
    ``kelt_plot.png`` say which was input and which output. A mention found in a file's text says
    neither, so :class:`DerivesFromDeclaredReference` takes its direction from the earliest credible
    time. A pair is drawn within ``co_mentioned`` (two files that one script's mentions name, with
    the script handed over as ``describing``), and three things must hold. Each is a separate
    abstention:

    1. The describing file is a script: a resolved classification whose value is one of
       ``describing_formats``.
    2. The script reads the source and writes the target, by the direction its calls recorded
       (``script.file_names``), and neither the other way round. A script that reads and writes
       both gives no direction.
    3. Both names resolved at the rule's floor (``minimum_resolution``). A script's bare name is
       resolved within its own folder or experiment only, and found by name there it is
       ``unique_basename``.

    This is testimony and not proof. The script states what it would do and not what a run did,
    which is why the rule proposes and never asserts. Nothing temporal is read.
    """

    name = "derives_from_script_calls"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the target written from the source by one script, or None."""
        found = self._calls(subject, parameters)
        if isinstance(found, str):
            return None
        described, read, written = found
        return (
            SupportComponent(
                name="read_and_written_by_one_script",
                family="declared_reference",
                value=1.0,
                observations=tuple(sorted({*read, *written, described})),
            ),
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say which of the three things did not hold."""
        found = self._calls(subject, parameters)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _calls(
        self, subject: object, parameters: Mapping[str, object]
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]] | str:
        """The script's claim, and the mentions reading the source and writing the target; or why
        not."""
        describing = getattr(subject, "describing", None)
        if getattr(subject, "within", None) != CO_MENTION_INDEX or describing is None:
            return "the pair was not drawn from one file's mentions"
        described = _describes(describing, parameters, ("describing_formats",))
        if described is None:
            return "the file naming both is not a script"
        floor = _RESOLUTION_ORDER.get(str(parameters.get("minimum_resolution", "exact")), 0)
        script = describing.entry.entry_id
        ends = {
            end: getattr(getattr(getattr(subject, end, None), "entry", None), "entry_id", None)
            for end in ("source", "target")
        }
        called: dict[tuple[str | None, str], list[str]] = {}
        below = False
        for resolution in getattr(subject, "resolutions", ()):
            direction = getattr(resolution.mention, "direction", "")
            if not direction or resolution.mention.entry_id != script:
                continue
            if _RESOLUTION_ORDER[_resolution_of(resolution.outcome)] > floor:
                below = True
                continue
            called.setdefault((resolution.target_id, direction), []).append(
                resolution.mention.observation_id
            )
        read = called.get((ends["source"], "read"), [])
        written = called.get((ends["target"], "written"), [])
        if not read or not written:
            if below:
                return "an end was named by the script's calls only below the rule's floor"
            return "the script's calls do not read the source and write the target"
        if called.get((ends["source"], "written")) or called.get((ends["target"], "read")):
            return "the script reads and writes one of the two, which gives no direction"
        return described, tuple(sorted(set(read))), tuple(sorted(set(written)))


class DerivesFromStageTransition(RanksPairEnds):
    """A stage step between two files, which corroborates a derivation and never creates one.

    A projection comes from an acquisition, and a figure comes from a table. Versions 1 to 3
    proposed a derivation wherever two files drawn within a name family or an identity sat on a
    declared step of a pipeline, and took the pair's direction from the step. Version 4 proposes
    nothing. A stage assignment is not a central aspect of a file's identity, and the engine finds
    all derivations before it assigns stages. A derivation is found from its own evidence:
    the acquisition a file is named for, a script's calls, a describing file, or a name identity.
    That evidence, or time, orders the pair. A stage step that agrees with a derivation is a
    component of the derivation's support (``stage_step``). A stage that disagrees with one is a
    question for a person (``stage_disagrees_with_a_derivation``).

    The predicate is declared and evaluable, and it abstains on every pair, so a rulebook that
    names it loads and says why it proposed nothing. Its ends are never ranked (``end_rank`` is
    None), so the drawing forms no pair for it at all. Its parameters stay declared, since shipped
    rules still set them.
    """

    name = "derives_from_stage_transition"
    version = "4"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Propose nothing: a stage step corroborates a derivation and never creates one."""
        del subject, parameters
        return None

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why a stage step proposes nothing."""
        del subject, parameters
        return Abstention(
            reason=(
                "a stage step corroborates a derivation found by its own evidence and never "
                "creates one"
            )
        )

    def end_rank(self, records: object, parameters: Mapping[str, object]) -> int | None:
        """None for every end: no pair is ever supported, so none is formed."""
        del records, parameters
        return None

    def least_rise(self, parameters: Mapping[str, object]) -> int:
        """No rise is read, since no end is ranked."""
        del parameters
        return 0


class DerivesFromNamedAcquisition(DrawsFromTheClassKey):
    """Propose that a file named for an acquisition was made from it.

    A tool names what it writes after the movie it read (``zorp_kelt_001_results.csv`` from
    ``kelt_001.nd2``). The name held whole is the evidence and gives the direction: the file named
    for an acquisition is made from it. Nothing here reads a stage. A tile that states none and an
    analysis that states one are proposed alike.

    The pairs are drawn by the key (:class:`~ingence.rulebook.registry.DrawsFromTheClassKey`).
    Within the ``named_for_an_acquisition`` grouping, whose class key names the acquisition, each
    member is paired with that acquisition and with nothing else. Two outputs of one acquisition
    are siblings, unless other evidence joins them: a script's calls, a describing file, a
    resolving mention, or one name identity across an experiment's folders. A time alone never
    makes one output the other's source. So a group never forms its square, and a file's one
    counterpart is its acquisition. The bound on a subject's candidates therefore never cuts one.

    A chain that a name identity joins stays link by link (``link_by_link_within``). Suppose a file
    of the target's own class of that grouping, in another folder than the target's, first existed
    after the acquisition and before the target. Then the identity's rule proposes the target from
    that file, and this rule leaves the acquisition's pair with the target. Without such evidence,
    a chain whose middle file's name a tool changed is proposed from the acquisition at each link.

    The support cites both ends' claims into the group. A stage step that agrees corroborates
    (``stage_step``). Clocks that disagree with the name's direction are said on the pair itself
    and weigh nothing (``made_before_its_source``, ``clocks_disagree``). A person who answers the
    pair settles its direction.
    """

    name = "derives_from_named_acquisition"
    version = "3"

    def __init__(self) -> None:
        # What a class holds is read once per class: the last class handed, by identity, with each
        # identity's members' clocks and folders.
        self._class: tuple[object, ...] | None = None
        self._class_within = ""
        self._class_peers: dict[str, list[tuple[str, str | None, _Clocks]]] = {}

    def key_source(self, key: str) -> str | None:
        """The acquisition a class of ``named_for_an_acquisition`` is keyed on."""
        return _acquisition_keyed(key)

    def sources(
        self, key: str, members: Sequence[object], parameters: Mapping[str, object]
    ) -> dict[str, str]:
        """Each member's source: the file whose whole name its name holds, where that is another
        member, else the acquisition."""
        del parameters
        acquisition = _acquisition_keyed(key)
        identifiers = {
            str(getattr(getattr(member, "entry", None), "entry_id", "")) for member in members
        }
        if acquisition is None or acquisition not in identifiers:
            return {}
        placed: dict[str, str] = {}
        for member in members:
            identifier = str(getattr(getattr(member, "entry", None), "entry_id", ""))
            if identifier and identifier != acquisition:
                placed[identifier] = _named_source(member, acquisition, identifiers)
        return placed

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the target made from the acquisition its name holds, or None."""
        found = self._read(subject, parameters)
        return None if isinstance(found, str) else found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that the source is not the acquisition the target's name holds, or a link skipped."""
        found = self._read(subject, parameters)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _read(
        self, subject: object, parameters: Mapping[str, object]
    ) -> tuple[SupportComponent, ...] | str:
        source, target = _end_id(subject, "source"), _end_id(subject, "target")
        key = _membership_key(subject, "target")
        acquisition = None if key is None else _acquisition_keyed(key)
        identifiers = {
            str(getattr(getattr(member, "entry", None), "entry_id", ""))
            for member in getattr(subject, "members", ())
        }
        expected = (
            None
            if acquisition is None
            else _named_source(getattr(subject, "target", None), acquisition, identifiers)
        )
        if source is None or source == target or expected != source:
            return "the source is not the file the target's name holds, nor its acquisition"
        within = parameters.get("link_by_link_within")
        if within and self._made_between(subject, str(within), _tolerance(parameters)):
            return (
                f"a file of the target's own {within}, in another folder, was made after the "
                "acquisition and before the target, and that grouping's rule proposes the link"
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
        step = _stage_step(subject, parameters)
        return (
            SupportComponent(
                name=_named_by(subject, source == acquisition),
                family="name_family",
                value=1.0,
                observations=cited,
            ),
            *_found_up(subject),
            *_clock_doubts(subject, parameters),
            *((step,) if step is not None else ()),
        )

    def _made_between(self, subject: object, within: str, tolerance: int) -> bool:
        """Whether a member of the target's class of ``within``, in another folder than the
        target's, first existed before the target by the clocks the two share: the identity's own
        rule then proposes the target from it, and the acquisition's pair is that link's closure."""
        from ingence.rulebook.predicates.classify import credible_clocks

        target = getattr(subject, "target", None)
        entry = getattr(target, "entry", None)
        if entry is None:
            return False
        source = _end_id(subject, "source")
        mine = credible_clocks(entry)
        held = self._peers(tuple(getattr(subject, "members", ())), within)
        for key in _class_keys(target, within):
            for identifier, folder, clocks in held.get(key, ()):
                if identifier in (entry.entry_id, source) or folder == entry.parent_entry_id:
                    continue
                if _ordered_by_kind(clocks, mine, tolerance).direction > 0:
                    return True
        return False

    def _peers(
        self, members: tuple[object, ...], within: str
    ) -> dict[str, list[tuple[str, str | None, _Clocks]]]:
        """Each class of ``within`` the members hold, with the folder and clocks of each member;
        once per class."""
        from ingence.rulebook.predicates.classify import credible_clocks

        if members is self._class and within == self._class_within:
            return self._class_peers
        peers: dict[str, list[tuple[str, str | None, _Clocks]]] = {}
        for member in members:
            entry = getattr(member, "entry", None)
            if entry is None:
                continue
            keys = _class_keys(member, within)
            if keys:
                clocks = credible_clocks(entry)
                for key in keys:
                    peers.setdefault(key, []).append(
                        (str(entry.entry_id), entry.parent_entry_id, clocks)
                    )
        self._class, self._class_within, self._class_peers = members, within, peers
        return peers


def _key_of(key: str) -> dict[str, object]:
    """A class key the named-beside pass wrote, as the mapping it is, or empty."""
    try:
        value = json.loads(key)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _identifiers(value: object) -> tuple[str, ...]:
    """A key's list of entry identifiers, or none."""
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _member_ids(members: Sequence[object]) -> set[str]:
    return {
        str(identifier)
        for identifier in (
            getattr(getattr(member, "entry", None), "entry_id", None) for member in members
        )
        if identifier is not None
    }


def _cited(subject: object) -> tuple[str, ...]:
    """Both ends' claims into the pair's class, as a support component cites them."""
    return tuple(
        sorted(
            {
                str(getattr(subject, "source_membership", "")),
                str(getattr(subject, "target_membership", "")),
            }
            - {""}
        )
    )


class DerivesIntoASeriesSummary(PairsWithinTheClass):
    """Propose that a table named for a series of numbered files beside it was made from each of
    them.

    Take ``zeph_results.xlsx`` beside ``zeph_001.nd2`` ... ``zeph_010.nd2``. The table's name is
    the series' shared stem and a word that says it holds results, so it summarizes the series. The
    rule proposes one pair from each member to the table. This is what the candidate model and the
    Graph can show, because a proposal that names the series whole has no end to draw to. Two
    members of the series are siblings and are never paired. The class's key, which the grouping
    ``named_for_a_series`` writes, names the tables, so each member's counterparts are the tables
    alone. The bound on a subject's candidates therefore never cuts a member's pair, however long
    the series.
    """

    name = "derives_into_a_series_summary"
    version = "1"

    def pairs_within(
        self, key: str, members: Sequence[object], parameters: Mapping[str, object]
    ) -> Mapping[str, tuple[str, ...]]:
        """Each member of the series with the tables named for it."""
        del parameters
        held = _member_ids(members)
        tables = tuple(
            table for table in _identifiers(_key_of(key).get("summaries")) if table in held
        )
        if not tables:
            return {}
        return dict.fromkeys(sorted(held - set(tables)), tables)

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for a member made into the table named for its series, or None."""
        found = self._read(subject)
        return None if isinstance(found, str) else found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say the target is not a table named for the source's series, or the source is one."""
        found = self._read(subject)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _read(self, subject: object) -> tuple[SupportComponent, ...] | str:
        key = _membership_key(subject, "target")
        tables = set(_identifiers(_key_of(key).get("summaries"))) if key is not None else set()
        source, target = _end_id(subject, "source"), _end_id(subject, "target")
        if target not in tables or source in tables or source is None:
            return "the target is no table named for the source's series, or the source is one"
        return (
            SupportComponent(
                name="a_table_named_for_its_series",
                family="name_family",
                value=1.0,
                observations=_cited(subject),
            ),
        )


#: The pairs a class of one name in sibling folders keys: directed by the folders' words, and the
#: rest, once each.
_DIRECTED_PAIRS = "derives"
_RELATED_PAIRS = "related"


def _keyed_pairs(key: str | None, parameters: Mapping[str, object]) -> set[tuple[str, str]]:
    """The pairs a class key names for a rule: the directed ones, or the related ones."""
    if key is None:
        return set()
    listed = _key_of(key).get(
        _RELATED_PAIRS
        if str(parameters.get("direction", "directed")) == "undirected"
        else _DIRECTED_PAIRS
    )
    if not isinstance(listed, list):
        return set()
    return {
        (str(pair[0]), str(pair[1])) for pair in listed if isinstance(pair, list) and len(pair) == 2
    }


class DerivesWithinOneNameBeside(PairsWithinTheClass):
    """Propose a derivation, or a relation, between two files of one name in sibling folders.

    Take ``images/a.tif`` and ``masks/a.tif``. One name in two folders of one parent is about one
    thing. The folders' own words give the direction where they can. A folder whose words mean it
    holds what came first (raw, images, input) is the source's, and a folder whose words mean what
    was made from them (labels, masks, ground truth, output) is the product's. The grouping
    ``one_name_in_sibling_folders`` reads them from the Dictionary's senses and writes the pairs
    into its class's key. With ``direction: directed``, each such pair is proposed as made from the
    source. With ``direction: undirected``, every other pair is proposed once as related, from the
    end whose identifier sorts first, since nothing says which was made from which
    (``left/a.tif``, ``right/a.tif``).

    A pair that another grouping already joins is that grouping's (``unless_joined_by``). Two files
    of one name identity within their experiment are proposed by that identity's rules, which read
    their clocks. Two copies of one file are the copy rules'. Neither is proposed again here.
    """

    name = "derives_within_one_name_beside"
    version = "1"

    def pairs_within(
        self, key: str, members: Sequence[object], parameters: Mapping[str, object]
    ) -> Mapping[str, tuple[str, ...]]:
        """The pairs the key names for the rule's direction, each source with its targets."""
        held = _member_ids(members)
        paired: dict[str, list[str]] = {}
        for source, target in sorted(_keyed_pairs(key, parameters)):
            if source in held and target in held:
                paired.setdefault(source, []).append(target)
        return {source: tuple(targets) for source, targets in paired.items()}

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the pair, directed or undirected as the rule asks, or None."""
        found = self._read(subject, parameters)
        return None if isinstance(found, str) else found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why the pair is not this rule's."""
        found = self._read(subject, parameters)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _read(
        self, subject: object, parameters: Mapping[str, object]
    ) -> tuple[SupportComponent, ...] | str:
        pair = (_end_id(subject, "source") or "", _end_id(subject, "target") or "")
        if pair not in _keyed_pairs(_membership_key(subject, "target"), parameters):
            return (
                "the folders' words do not direct the pair this way"
                if str(parameters.get("direction", "directed")) != "undirected"
                else "the folders' words direct the pair, or it is proposed once the other way"
            )
        declared = parameters.get("unless_joined_by") or ()
        groupings = (
            [declared]
            if isinstance(declared, str)
            else [str(item) for item in declared]
            if isinstance(declared, (list, tuple))
            else []
        )
        for grouping in groupings:
            if _class_keys(getattr(subject, "source", None), str(grouping)) & _class_keys(
                getattr(subject, "target", None), str(grouping)
            ):
                return f"the two files share a class of {grouping}, whose own rules read the pair"
        directed = str(parameters.get("direction", "directed")) != "undirected"
        return (
            SupportComponent(
                name="one_name_in_sibling_folders",
                family="name_family",
                value=1.0,
                observations=_cited(subject),
            ),
            *(
                (SupportComponent(name="folder_words_direct_it", family="direction", value=0.0),)
                if directed
                else ()
            ),
        )


def _tolerance(parameters: Mapping[str, object]) -> int:
    """The rule's tolerance in nanoseconds: ``tolerance_seconds``, a minute where it says none."""
    declared = parameters.get("tolerance_seconds", TOLERANCE_SECONDS)
    seconds = (
        declared
        if isinstance(declared, int) and not isinstance(declared, bool)
        else TOLERANCE_SECONDS
    )
    return seconds * 1_000_000_000


def _class_keys(records: object, grouping: str) -> set[str]:
    """The classes of one grouping an end's resolved claims place it in."""
    return {
        str(claim.object_reference)
        for claim in getattr(records, "claims", ())
        if isinstance(claim, Claim)
        and claim.rule_id == grouping
        and claim.resolution_status is ResolutionStatus.RESOLVED
        and claim.object_reference is not None
    }


class DerivesWithinANameIdentity:
    """Propose a derivation between two files of one name identity, ordered by time.

    Two files that agree on every component of their names but for stage words and the extension
    (``vorn_001_raw.tif``, ``vorn_001_analysis.csv``) are about one sample and one acquisition.
    The identity says nothing about which was made from which. The earliest credible time orders
    the pair, read by kind (:func:`_order`). Two content clocks order it, and so do two filesystem
    clocks neither of which shows a copy. A content clock against a filesystem one, or a file born
    after it was changed, orders nothing. Where no time orders the pair, the rule declaring
    ``direction: undirected`` proposes it once, undirected.

    A chain is proposed link by link (version 2). A pair is left where another file of the
    identity first existed after the source and before the target, each by more than
    ``tolerance_seconds``. So a raw image, its processed image and the analysis of that are two
    derivations and never three. The rule reads this once per class from the members the pair is
    handed. Two siblings of one identity, in two folders and made an hour apart, are read as a
    chain without a warning. The identity across folders is the evidence, and
    ``stage_disagrees_with_a_derivation`` raises only a stage earlier than its source's, never two
    on one rung.

    The rule can compare across folders only, where the rule says so (``across_folders_only``).
    The evidence is then one name identity across an experiment's folders. Two outputs in one
    folder that share an identity are siblings, which time alone never orders into a derivation.

    A pair that the files named for one acquisition both belong to is that rule's
    (``unless_joined_by``, the grouping keyed on the acquisition). Its chain runs through every
    file named for the acquisition. A stage step that agrees with the time corroborates
    (``stage_step``). A stage step that disagrees is raised for a person and never reverses the
    pair.
    """

    name = "derives_within_a_name_identity"
    version = "3"

    def __init__(self) -> None:
        # Each member's clocks and folder, read once per class.
        self._class: tuple[object, ...] | None = None
        self._clocks: dict[str, _Clocks] = {}
        self._sorted: dict[str, list[tuple[int, str]]] = {}

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the pair, directed or undirected as the rule asks, or None."""
        found = self._read(subject, parameters)
        return None if isinstance(found, str) else found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why the pair is not this rule's: another rule's, a link skipped, or no order."""
        found = self._read(subject, parameters)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _read(
        self, subject: object, parameters: Mapping[str, object]
    ) -> tuple[SupportComponent, ...] | str:
        cheap = _refused_before_time(subject, parameters)
        if cheap is not None:
            return cheap
        if parameters.get("across_folders_only", False) is True and _one_folder(subject):
            return (
                "two files of one folder sharing a name identity are siblings, and time alone "
                "never makes one the other's source"
            )
        grouping = parameters.get("unless_joined_by")
        if grouping and _joined_by_an_acquisition(subject, str(grouping)):
            return (
                f"one end is the acquisition the other's name holds, and {grouping}'s own rule "
                "proposes the pair"
            )
        self._of_class(tuple(getattr(subject, "members", ())))
        order = _order(subject, parameters, self._clocks)
        directed = _directed_by(subject, parameters, order)
        if isinstance(directed, str):
            return directed
        # A file of the target's own folder is its sibling and never its source, so it never
        # stands between where the rule pairs across folders only.
        home = (
            getattr(
                getattr(getattr(subject, "target", None), "entry", None), "parent_entry_id", None
            )
            if parameters.get("across_folders_only", False) is True
            else None
        )
        if order.direction > 0 and self._made_between(order, _tolerance(parameters), home):
            return (
                "another file of the identity was made after the source and before the target, so "
                "the chain is proposed link by link"
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
                name="name_identity", family="name_family", value=1.0, observations=cited
            ),
            *directed,
            *(_clock_doubts(subject, parameters) if directed else ()),
        )

    def _of_class(self, members: tuple[object, ...]) -> None:
        """Each member's clocks and each kind's sorted instants, once per class."""
        if not members or members is self._class:
            return
        from ingence.rulebook.predicates.classify import credible_clocks

        clocks: dict[str, _Clocks] = {}
        folders: dict[str, str] = {}
        for member in members:
            entry = getattr(member, "entry", None)
            if entry is not None:
                clocks[str(entry.entry_id)] = credible_clocks(entry)
                folders[str(entry.entry_id)] = str(entry.parent_entry_id or "")
        self._class, self._clocks = members, clocks
        self._sorted = {
            kind: sorted(
                (held[at][0], folders[identifier])  # type: ignore[index]
                for identifier, held in clocks.items()
                if held[at] is not None
            )
            for at, kind in ((0, "content"), (1, "filesystem"))
        }

    def _made_between(self, order: _Order, tolerance: int, home: str | None) -> bool:
        """Whether a member's clock of the order's kind lies between the two ends', beyond the
        tolerance on each side, in another folder than ``home`` where one is given."""
        held = self._sorted.get(order.kind, [])
        at = bisect.bisect_right(held, (order.source + tolerance, "\uffff"))
        for instant, folder in held[at:]:
            if instant >= order.target - tolerance:
                return False
            if home is None or folder != str(home):
                return True
        return False


#: How far apart two credible times must be before one orders a pair, where a rule says nothing:
#: a minute, as ``derivation_follows_the_clock`` tolerates a small inversion.
TOLERANCE_SECONDS = 60


def _end_id(subject: object, end: str) -> str | None:
    """One end's entry identifier."""
    entry = getattr(getattr(subject, end, None), "entry", None)
    identifier = getattr(entry, "entry_id", None)
    return None if identifier is None else str(identifier)


def _acquisition_keyed(key: str) -> str | None:
    """The acquisition a ``named_for_an_acquisition`` class key names, or None."""
    try:
        value = json.loads(key)
    except ValueError:
        return None
    acquisition = value.get("acquisition") if isinstance(value, dict) else None
    return str(acquisition) if acquisition else None


def _membership_key(subject: object, end: str) -> str | None:
    """The class key one end's claim into the pair's class names, read from its own claims."""
    membership = getattr(subject, f"{end}_membership", None)
    for claim in getattr(getattr(subject, end, None), "claims", ()):
        if isinstance(claim, Claim) and claim.claim_id == membership:
            return None if claim.object_reference is None else str(claim.object_reference)
    return None


#: One file's content clock and filesystem clock, each with its source, or None.
_Clocks = tuple[tuple[int, str] | None, tuple[int, str] | None]


@dataclass(frozen=True, slots=True)
class _Order:
    """Which end came first, by which kind of clock, and each end's instant of that kind."""

    direction: int = 0
    kind: str = ""
    clocks: str = ""
    source: int = 0
    target: int = 0


def _one_folder(subject: object) -> bool:
    """Whether the pair's two ends lie in one folder."""
    folders = {
        getattr(getattr(getattr(subject, end, None), "entry", None), "parent_entry_id", None)
        for end in ("source", "target")
    }
    return len(folders) == 1


def _found_up(subject: object) -> tuple[SupportComponent, ...]:
    """Where the whole name the target holds was found above its own folder, said on the pair and
    weighing nothing: ``found_3_folders_up``."""
    named = getattr(getattr(subject, "target", None), "named_acquisition", None)
    levels = getattr(named, "levels_up", 0) if named is not None else 0
    if not isinstance(levels, int) or levels <= 0:
        return ()
    return (SupportComponent(name=f"found_{levels}_folders_up", family="name_family", value=0.0),)


def _named_by(subject: object, from_the_key: bool) -> str:
    """What a pair's evidence is called: the acquisition's name held whole, an image's name with a
    word meaning an annotation, or another file's whole name."""
    named = getattr(getattr(subject, "target", None), "named_acquisition", None)
    if named is not None and getattr(named, "annotates", "") == _end_id(subject, "source"):
        return "an_annotation_named_for_its_image"
    if from_the_key and _concluded_an_acquisition(getattr(subject, "source", None)):
        return "named_for_the_acquisition"
    return "named_for_its_whole_file_name"


#: The stage an acquisition's classification concludes (``acquisition_stage_from_format``).
_ACQUISITION_STAGE = "acquisition"


def _concluded_an_acquisition(records: object) -> bool:
    """Whether a standing classification concludes the file an acquisition: a group
    of files named for another's whole name may be keyed on a file that is none."""
    return any(
        isinstance(claim, Claim)
        and claim.predicate.startswith("classifies.")
        and claim.resolution_status is ResolutionStatus.RESOLVED
        and claim.object_reference == _ACQUISITION_STAGE
        for claim in getattr(records, "claims", ())
    )


def _named_source(records: object, acquisition: str, members: set[str]) -> str:
    """A file's source in its acquisition's group: the file whose whole name its name holds, where
    that is a member, else the acquisition."""
    named = getattr(records, "named_acquisition", None)
    source = getattr(named, "source", "") if named is not None else ""
    return source if source and source in members else acquisition


def _joined_by_an_acquisition(subject: object, grouping: str) -> bool:
    """Whether one end is the acquisition the other's name holds, by the grouping keyed on it."""
    ends = {_end_id(subject, "source"), _end_id(subject, "target")}
    shared = _class_keys(getattr(subject, "source", None), grouping) & _class_keys(
        getattr(subject, "target", None), grouping
    )
    return any(_acquisition_keyed(key) in ends for key in shared)


def _clock_doubts(
    subject: object, parameters: Mapping[str, object]
) -> tuple[SupportComponent, ...]:
    """What the two ends' clocks say against a derivation's direction, said on the pair.

    Each is a component weighing nothing, where a person who answers the pair settles its
    direction. ``made_before_its_source``: the target first existed before the source by one kind
    of clock beyond the tolerance. ``clocks_disagree``: both ends carry a content and a filesystem
    clock, and the two kinds order them opposite ways.
    """
    from ingence.rulebook.predicates.classify import credible_clocks

    ends = [getattr(getattr(subject, end, None), "entry", None) for end in ("source", "target")]
    if ends[0] is None or ends[1] is None:
        return ()
    first, second = credible_clocks(ends[0]), credible_clocks(ends[1])
    tolerance = _tolerance(parameters)
    doubts: list[str] = []
    if _order(subject, {"tolerance_seconds": tolerance // 1_000_000_000}).direction < 0:
        doubts.append("made_before_its_source")
    kinds = [_compare(first[at], second[at], tolerance) for at in (0, 1)]
    if kinds[0] * kinds[1] < 0:
        doubts.append("clocks_disagree")
    return tuple(SupportComponent(name=name, family="direction", value=0.0) for name in doubts)


def _compare(first: tuple[int, str] | None, second: tuple[int, str] | None, tolerance: int) -> int:
    """1 where ``first`` is earlier than ``second`` beyond the tolerance, -1 where later, else 0."""
    if first is None or second is None:
        return 0
    gap = second[0] - first[0]
    return 1 if gap > tolerance else -1 if -gap > tolerance else 0


def _refused_before_time(subject: object, parameters: Mapping[str, object]) -> str | None:
    """The refusals read from the parameters and identifiers alone, before any clock."""
    direction = str(parameters.get("direction", "earliest_first"))
    if direction != "undirected" and parameters.get("ordered_by_time", True) is False:
        return "the rule orders no pair by time, so it proposes none as made from the other"
    if direction == "undirected" and (_end_id(subject, "source") or "") > (
        _end_id(subject, "target") or ""
    ):
        return "an undirected pair is proposed once, from the end whose identifier sorts first"
    return None


def _order(
    subject: object, parameters: Mapping[str, object], known: Mapping[str, _Clocks] | None = None
) -> _Order:
    """Which end came first by its earliest credible time, read by kind.

    Two content clocks order a pair. Two filesystem clocks do where neither file was born after it
    was changed (a copy, whose birth is when it landed). A content clock against a filesystem one
    orders nothing, so a dive that reads one end's content never reverses a pair the filesystem
    ordered. A gap within ``tolerance_seconds`` orders nothing. ``ordered_by_time: false`` reads
    no clock at all.
    """
    from ingence.rulebook.predicates.classify import credible_clocks

    if parameters.get("ordered_by_time", True) is False:
        return _Order()
    ends = []
    for end in ("source", "target"):
        entry = getattr(getattr(subject, end, None), "entry", None)
        if entry is None:
            return _Order()
        held = (known or {}).get(str(entry.entry_id))
        ends.append(held if held is not None else credible_clocks(entry))
    return _ordered_by_kind(ends[0], ends[1], _tolerance(parameters))


def _ordered_by_kind(first_clocks: _Clocks, second_clocks: _Clocks, tolerance: int) -> _Order:
    """Which of two files came first by the clocks of one kind they share (``_order``).

    Two filesystem clocks, neither a copy's, order the pair. A dive that reads a content clock on
    one end never takes that order away, and where the content clocks disagree with it, that doubt
    is said on the pair (:func:`_clock_doubts`). Two content clocks order a pair the filesystem
    cannot. A content clock is never read against a filesystem one, and a copy's clock orders
    nothing.
    """
    (source_content, source_files), (target_content, target_files) = first_clocks, second_clocks
    copied = any(
        clock is not None and clock[1] == "modified_before_born"
        for clock in (source_files, target_files)
    )
    if source_files is not None and target_files is not None and not copied:
        kind, first, second = "filesystem", source_files, target_files
    elif source_content is not None and target_content is not None:
        kind, first, second = "content", source_content, target_content
    elif copied:
        return _Order(clocks="a copy's clock")
    else:
        return _Order(clocks="clocks of two kinds")
    gap = second[0] - first[0]
    names = f"{first[1]}_then_{second[1]}" if gap >= 0 else f"{second[1]}_then_{first[1]}"
    direction = 1 if gap > tolerance else -1 if -gap > tolerance else 0
    return _Order(direction, kind, names, first[0], second[0])


def _directed_by(
    subject: object, parameters: Mapping[str, object], order: _Order
) -> tuple[SupportComponent, ...] | str:
    """What says a pair's direction where its evidence gives none, or why the pair is not the
    rule's.

    ``direction: earliest_first``: the source is the end whose credible time is earlier by more
    than the tolerance. The clocks are named in a component that weighs nothing, because a clock
    orders a pair and supports none. A stage step that agrees with the order corroborates.
    ``direction: undirected``: no time orders the pair, and it is proposed once, from the end whose
    identifier sorts first, with nothing to add.
    """
    if str(parameters.get("direction", "earliest_first")) == "undirected":
        if order.direction:
            return (
                "a credible time orders the pair, so it is proposed as made from the earlier, "
                "not only as related"
            )
        return ()
    if order.direction == 0:
        return (
            "no credible time of one kind orders the pair, so it is proposed as related, with "
            "which was made from the other not known"
        )
    if order.direction < 0:
        return "the target's earliest credible time is earlier than the source's"
    step = _stage_step(subject, parameters)
    return (
        SupportComponent(name=f"earlier_by_{order.clocks}", family="direction", value=0.0),
        *((step,) if step is not None else ()),
    )


def _stage_step(subject: object, parameters: Mapping[str, object]) -> SupportComponent | None:
    """A stage step agreeing with a derivation found by evidence, as its corroboration.

    Both ends hold a stage ``stage_transitions`` names, and the latest the target holds stands on
    a later rung than the latest the source holds. Never what makes a pair; None where the rule
    names no transitions, an end holds no stage, or the step does not climb.
    """
    transitions = _transitions(parameters)
    if not transitions:
        return None
    rung = _rungs(transitions)
    named = frozenset(rung)
    source = _stages(getattr(subject, "source", None), named)
    target = _stages(getattr(subject, "target", None), named)
    if not source or not target:
        return None
    if max(rung[stage] for stage in target) <= max(rung[stage] for stage in source):
        return None
    return SupportComponent(
        name="stage_step",
        family="stage_transition",
        value=1.0,
        observations=tuple(sorted({*source.values(), *target.values()})),
    )


def _rungs(transitions: Mapping[str, str]) -> dict[str, int]:
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


#: The shipped rule's resolution scale, strongest first (``references_a_declared_output``).
_RESOLUTION_ORDER = {"exact": 0, "unique_suffix": 1, "unique_basename": 2, "ambiguous": 3}


def _resolution_of(outcome: str) -> str:
    """Return where one resolution sits on the rule's scale.

    In the mentioning file's own folder, or rooted and matched exactly, is ``exact``; found
    only from a root of the pool is ``unique_suffix``, the path being a suffix of the match;
    found only by its last name is ``unique_basename``, below the shipped rule's default floor.
    """
    if outcome == "by_name":
        return "unique_basename"
    return "unique_suffix" if outcome == "from_root" else "exact"


def _both_ends_resolved(subject: object, parameters: Mapping[str, object]) -> bool:
    """Whether each end of a co-mentioned pair was named at the rule's floor or better.

    A derivation needs its ends as firmly as a reference does. On one real drive, a run log
    whose paths resolved only by their last name proposed many derivations among its files. Where
    the rule declares no floor, it takes the shipped reference rule's.
    """
    floor = _RESOLUTION_ORDER.get(str(parameters.get("minimum_resolution", "unique_suffix")), 1)
    ends = {getattr(getattr(subject, end, None), "entry", None) for end in ("source", "target")}
    firm = {
        resolution.target_id
        for resolution in _undirected(subject)
        if _RESOLUTION_ORDER[_resolution_of(resolution.outcome)] <= floor
    }
    return all(getattr(entry, "entry_id", None) in firm for entry in ends) and len(ends) == 2


def _undirected(subject: object) -> tuple[Resolution, ...]:
    """The pair's resolutions of mentions found in a file's text, which say no direction.

    A script's own calls are mentions too, each with the direction its call gives. They are the
    script-call rule's evidence. A describing file's reading of a pair rests on what its text
    names and nothing else, so no pair is proposed twice from one call.
    """
    return tuple(
        resolution
        for resolution in getattr(subject, "resolutions", ())
        if not getattr(resolution.mention, "direction", "")
    )


def _called_both_ways(subject: object) -> bool:
    """Whether the describing file's own calls read one end of the pair and write the other."""
    describing = getattr(getattr(subject, "describing", None), "entry", None)
    script = getattr(describing, "entry_id", None)
    called = {
        (resolution.target_id, resolution.mention.direction)
        for resolution in getattr(subject, "resolutions", ())
        if getattr(resolution.mention, "direction", "") and resolution.mention.entry_id == script
    }
    ends = [
        getattr(getattr(getattr(subject, end, None), "entry", None), "entry_id", None)
        for end in ("source", "target")
    ]
    return any(
        (first, "read") in called and (second, "written") in called
        for first, second in (ends, ends[::-1])
    )


def _describes(
    records: object,
    parameters: Mapping[str, object],
    keys: tuple[str, ...] = ("describing_formats", "describing_roles"),
) -> str | None:
    """Return the claim that makes a file a description of a process, or None.

    A resolved classification whose value is a describing file type or a describing role (the
    parameters ``keys`` names). Read from the claims the earlier phases published, so a format a
    defeater retracted describes nothing.
    """
    wanted = {
        str(value)
        for key in keys
        for value in (parameters.get(key) or ())  # type: ignore[attr-defined]
    }
    for claim in sorted(getattr(records, "claims", ()), key=lambda claim: claim.claim_id):
        if (
            isinstance(claim, Claim)
            and claim.predicate.startswith(f"{RuleEffect.CLASSIFIES.value}.")
            and claim.resolution_status is ResolutionStatus.RESOLVED
            and claim.object_reference in wanted
        ):
            return claim.claim_id
    return None


def _transitions(parameters: Mapping[str, object]) -> dict[str, str]:
    """Return a stage mapping parameter as the steps it declares, from stage to stage."""
    declared = parameters.get("stage_transitions")
    if not isinstance(declared, Mapping):
        return {}
    return {str(before): str(after) for before, after in declared.items()}


def _stages(records: object, named: frozenset[str]) -> dict[str, str]:
    """Return an end's stages that a mapping names, each with the claim that concluded it.

    Read from the claims the classifying phase published and left standing, so the stage
    is a rule's conclusion rather than a word in a name, and a stage a defeater retracted
    is not a stage at all.
    """
    found: dict[str, str] = {}
    for claim in sorted(getattr(records, "claims", ()), key=lambda claim: claim.claim_id):
        if (
            isinstance(claim, Claim)
            and claim.predicate.startswith(f"{RuleEffect.CLASSIFIES.value}.")
            and claim.resolution_status is ResolutionStatus.RESOLVED
            and claim.object_reference is not None
            and claim.object_reference in named
        ):
            found.setdefault(claim.object_reference, claim.claim_id)
    return found


class DerivesFromAggregation:
    """Propose that one file was made from many, and not from one.

    Examples are a summary workbook built from a folder of tables, a figure panel assembled from a
    set of images, and a report covering everything under it. The one-to-many shape is different
    from everything else in this module and needs saying separately. A rule that scores pairs will
    either propose one weak edge per member, which buries the real relation in noise, or propose
    the single strongest and assert that the summary came from one of its twenty inputs.

    Acyclicity does the work that ordering cannot. An aggregate sits later than every member by
    construction. So the constraint that refuses cycles also refuses the reading where a member
    derives from the summary, without any rule having to decide which of twenty files came first.

    A grouping bounds the member set and a folder walk never does. "Every file below here" reaches
    an arbitrary distance and would make a top-level report derive from the entire pool. The
    aggregate's scope is an equivalence class, which is a stated respect somebody can read.

    Declared and not evaluable. It needs a group to aggregate over.
    """

    name = "derives_from_aggregation"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "derives_from_aggregation reads a group as the member set, and no phase "
            "hands a proposing predicate a group yet"
        )
        raise PredicateNotEvaluableError(message)


class EquivalentByMultiSignalDesign:
    """Propose that two units of work are the same design, or one dimension apart.

    The rule is meant to compare two experiments on shared conditions, shared vocabulary, name
    similarity and an explicit repeat marker. It asks whether they are replicates of one design or
    variants that differ in one dimension. Both readings are real, and the distinction between them
    is what users care about most. One means "do these agree" and the other means "what did changing
    this do".

    It is one mechanism at two settings. The scores are support components and the settings are
    parameters. So the difference between a replicate and a variant is readable in two sentences,
    and a reader does not have to infer it from two boolean expressions with inline constants.

    The dimension that is allowed to differ is named and not counted. "Differs in one thing" is not
    a claim anybody can check. "Differs in respect concentration and agrees on everything else" is.
    Naming the dimension also makes the variant relation useful, because the answer to "what did
    changing this do" has to say what was changed.

    The rule may not score on time, and that constraint bites hardest here. Two runs of one design
    usually are close in time. That makes temporal proximity look like excellent evidence, and it
    is exactly the thing that will group three unrelated efforts from one busy month. A date gap
    may bound which pairs are considered and may be shown beside the answer. It contributes
    nothing. The families declared below are the enforcement.

    Declared and not evaluable. It compares two members of a level coarser than a file, and
    nothing lifts entries to one yet.
    """

    name = "equivalent_by_multi_signal_design"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "equivalent_by_multi_signal_design compares two units of work at a level "
            "coarser than a file, and nothing lifts entries to that level yet"
        )
        raise PredicateNotEvaluableError(message)


class SucceedsByOrderedToken(PairsWithinTheClass):
    """Propose an order between two entries from a token that carries one.

    Examples of such a token are a version marker, a replicate index and a session number. The
    token supplies the order and the engine supplies nothing. This separates the rule from every
    other proposing rule here: there is no similarity, no threshold and no evidence to weigh, only
    a comparison between two values a person wrote.

    Ordering is not derivation, and the two are separate primitives for a reason. Version two does
    not come from version one. It replaces it. A later replicate is not made from an earlier one.
    Recording either as a derivation would put an ancestry claim into the provenance chain that
    nothing supports, and users trust the chain most.

    The comparison must be numeric. Sorted as strings, version ten precedes version nine, and the
    error appears only on the tenth iteration of something, which is long after anybody is
    checking. Where a token does not parse as a number, the rule abstains and does not fall back to
    string order, because a wrong order looks exactly like a right one.

    A series is required before an order means anything. Two indexed tokens in unrelated work are
    not comparable at all, and a separate constraint refuses the pair.

    The rule is evaluable. The series is a class of replicates of one condition (the grouping the
    rule draws within), and only neighbouring members are paired. The earlier member is the
    source and the next one is the target. So a class of ten forms nine pairs and never its
    square, and the chain closes to the total order that a series has. A class abstains whole
    (:func:`~ingence.rulebook.predicates.series.read_series`) when two parts of its names vary,
    when two members carry the same number, or when a value does not parse. It then forms no pair,
    and :meth:`explain_abstention` says which. A dose ladder is never a series: a dose is a
    condition, and a class of replicates holds its quantities fixed.
    """

    name = "succeeds_by_ordered_token"
    version = "2"

    def __init__(self) -> None:
        #: The last class read: its members (by identity, the tuple every pair of one class
        #: shares) and the reading, so the pairs of one class cost one reading.
        self._last: tuple[Sequence[object], SeriesReading] | None = None

    def _reading(
        self, members: Sequence[EntryRecords], parameters: Mapping[str, object]
    ) -> SeriesReading:
        last = self._last
        if last is not None and last[0] is members:
            return last[1]
        tolerance = parameters.get("tolerance_seconds", CLOCK_TOLERANCE_SECONDS)
        reading = read_series(
            members,
            counting_kinds(str(parameters.get("ordering_kind", "indexed"))),
            tolerance if isinstance(tolerance, int) else CLOCK_TOLERANCE_SECONDS,
        )
        self._last = (members, reading)
        return reading

    def pairs_within(
        self, key: str, members: Sequence[object], parameters: Mapping[str, object]
    ) -> Mapping[str, tuple[str, ...]]:
        """Each member and the next one in the class's order; nothing for a class with none."""
        del key
        entries: Sequence[EntryRecords] = members  # type: ignore[assignment]
        reading = self._reading(entries, parameters)
        return {
            earlier: (later,)
            for earlier, later in zip(reading.order, reading.order[1:], strict=False)
        }

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the target following the source in its class, or None."""
        found = self._read(subject, parameters)
        return None if isinstance(found, str) else found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why the class has no order, or that the two are not neighbours in it."""
        reading = self._reading_of(subject, parameters)
        if reading is not None and not reading.orders:
            return Abstention(
                status=(
                    ResolutionStatus.UNEXAMINED
                    if reading.unexamined
                    else ResolutionStatus.EXAMINED_NO_EVIDENCE
                ),
                reason=f"the class has no order: {reading.reason}",
            )
        found = self._read(subject, parameters)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _reading_of(
        self, subject: object, parameters: Mapping[str, object]
    ) -> SeriesReading | None:
        members = getattr(subject, "members", ())
        return self._reading(members, parameters) if members else None

    def _read(
        self, subject: object, parameters: Mapping[str, object]
    ) -> tuple[SupportComponent, ...] | str:
        reading = self._reading_of(subject, parameters)
        if reading is None:
            return "the pair was not drawn from a class, so there is no series to order"
        if not reading.orders:
            return f"the class has no order: {reading.reason}"
        source, target = _end_id(subject, "source"), _end_id(subject, "target")
        if source is None or target is None or source not in reading.places:
            return "the source is no member of the class's series"
        if reading.places.get(target) != reading.places[source] + 1:
            return "the target does not come directly after the source in the series"
        return (
            SupportComponent(
                name="the_next_number_in_the_series",
                family="ordered_token",
                value=1.0,
                observations=_cited(subject),
            ),
        )


class ReferencesByDeclaredPath:
    """Propose that one file points at another, and claim nothing further.

    Examples are a path in a script, a filename in a log, and a link in a document. ``references``
    is the only primitive that permits cycles and the only one that carries no implication about
    production, order or sameness. That makes it the honest home for every mention the engine
    finds and cannot interpret.

    Recording the mention separately from any reading of it is the design. The same mention may
    later support a derivation, or a configuration relation, or nothing at all, and those are
    different rules that read one observation. An engine that converted mentions straight into
    provenance would have no way to represent a script that names a file it never reads.

    A mentioned path may name nothing that exists, and that is a finding. A script that refers to a
    file absent from the pool is evidence that the pool is incomplete, which is more useful than
    the edge would have been. The reference resolution state already distinguishes an exact hit, a
    unique suffix match, an ambiguous one and an unresolved one. So the honest outcomes are
    available without inventing a target.

    The rule is evaluable. A pair is drawn within ``mentioned``: the mentioning file and an entry
    that one of its mentions resolved to (:mod:`ingence.infer.references`). What the rule adds is
    the floor. A mention resolved in the mentioning file's own folder, or a rooted path matched
    exactly under its root, is an ``exact`` resolution. A mention found only from a root of the
    pool is a ``unique_suffix``, because it rests on the laboratory treating that root as its
    base. Several matches never reach here: the resolution abstains on them, whatever
    ``minimum_resolution`` admits.

    The support names how the mention resolved and cites the observation that carried it, so a
    candidate walks back to the bytes that mentioned the path.
    """

    name = "references_by_declared_path"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the source referencing the target, or None."""
        if getattr(subject, "within", None) != MENTION_INDEX:
            return None
        floor = _RESOLUTION_ORDER.get(str(parameters.get("minimum_resolution", "exact")), 0)
        held = [
            resolution
            for resolution in getattr(subject, "resolutions", ())
            if _RESOLUTION_ORDER[_resolution_of(resolution.outcome)] <= floor
        ]
        if not held:
            return None
        return tuple(
            SupportComponent(
                name=outcome,
                family="declared_reference",
                value=1.0,
                observations=tuple(
                    sorted(
                        {
                            resolution.mention.observation_id
                            for resolution in held
                            if resolution.outcome == outcome
                        }
                    )
                ),
            )
            for outcome in sorted({resolution.outcome for resolution in held})
        )

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say whether nothing was resolved or a resolution fell below the rule's floor."""
        if not getattr(subject, "resolutions", ()):
            return Abstention(reason="no mention of the source resolved to the target")
        return Abstention(
            reason=(
                "the mention resolved only from a root of the pool, below the rule's "
                f"minimum_resolution of {parameters.get('minimum_resolution')}"
            )
        )


class ConcernsByFoundSense:
    """Propose that two entries are about the same thing, on one stated axis.

    ``concerns`` is symmetric and never transitive, and this predicate exists partly to make that
    visible where somebody will meet it. Two files that name one technique concern that technique.
    A third file that shares a different technique with the second concerns nothing of the first.
    An engine that composed the two hops would connect every file in the pool through four or five
    intermediate subjects.

    The relation is never a grouping, and the loader refuses the attempt. The temptation is strong
    because the relation is symmetric, and symmetric relations look like equivalences. The quotient
    of a non-transitive relation is not well defined, so the groups it produced would depend on the
    order in which pairs were visited.

    A single shared sense is weak evidence, and compounding is across senses and not spellings. Two
    files that both name one common technique share very little. Two files that share three
    distinct senses on one axis share something real. The rule carries the floor, and the axis is
    named so that sharing a format never compounds with sharing a subject.

    Declared and not evaluable. The senses exist per entry, and the proposing phase can draw a pair
    within a declared index. What is missing is this predicate's comparison of the senses that the
    two entries bound.
    """

    name = "concerns_by_found_sense"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "concerns_by_found_sense compares the senses two entries bound, and comparing "
            "them across a pair is not implemented"
        )
        raise PredicateNotEvaluableError(message)


@declare(
    "derives_from_name_family",
    version="1",
    summary="Propose derives where a derived file shares a name family with a source",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("minimum_support", "source_roles"),
    # Name similarity, family match and fuzzy support are three views of one fact
    # and share one family, so they cannot compound with each other. Nothing
    # temporal appears here, and that is the first constraint on proposing rules.
    support_families=("name_family", "shape_agreement", "same_parent"),
)
def _build_derives_from_name_family() -> Predicate:
    return DerivesFromNameFamily()


@declare(
    "derives_from_declared_reference",
    version="6",
    summary="Propose derives where a script or log names both the input and the output",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=(
        "describing_formats",
        "describing_roles",
        "direction",
        "maximum_separation",
        "minimum_resolution",
        "ordered_by_time",
        "stage_transitions",
        "tolerance_seconds",
    ),
    # ``direction`` weighs nothing: a clock orders a pair and supports none.
    support_families=("declared_reference", "direction", "name_family", "stage_transition"),
)
def _build_derives_from_declared_reference() -> Predicate:
    return DerivesFromDeclaredReference()


@declare(
    "derives_from_stage_transition",
    version="4",
    summary="Propose derives where two files sit on a declared step of a pipeline",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=(
        "stage_transitions",
        "refuse_siblings_on_one_rung",
        "maximum_rungs_skipped",
        "unless_sharing",
    ),
    support_families=("stage_transition", "name_family", "same_parent"),
)
def _build_derives_from_stage_transition() -> Predicate:
    return DerivesFromStageTransition()


@declare(
    "derives_from_named_acquisition",
    version="3",
    summary="Propose derives from an acquisition to each file whose name holds it whole",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("link_by_link_within", "stage_transitions", "tolerance_seconds"),
    # ``direction`` weighs nothing: a clock's doubt is said on the pair and supports none.
    support_families=("direction", "name_family", "stage_transition"),
    reach="pair",
)
def _build_derives_from_named_acquisition() -> Predicate:
    return DerivesFromNamedAcquisition()


@declare(
    "derives_within_a_name_identity",
    version="3",
    summary="Propose derives between two files of one name identity, the earlier as the source",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=(
        "across_folders_only",
        "direction",
        "ordered_by_time",
        "stage_transitions",
        "tolerance_seconds",
        "unless_joined_by",
    ),
    # ``direction`` weighs nothing: a clock orders a pair and supports none.
    support_families=("direction", "name_family", "stage_transition"),
    reach="pair",
)
def _build_derives_within_a_name_identity() -> Predicate:
    return DerivesWithinANameIdentity()


@declare(
    "derives_into_a_series_summary",
    version="1",
    summary="Propose derives from each numbered file of a series to the table named for it",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    support_families=("name_family",),
    reach="pair",
)
def _build_derives_into_a_series_summary() -> Predicate:
    return DerivesIntoASeriesSummary()


@declare(
    "derives_within_one_name_beside",
    version="1",
    summary="Propose derives, or a relation, between files of one name in sibling folders",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("direction", "unless_joined_by"),
    # ``direction`` weighs nothing: the folders' words order the pair and support none.
    support_families=("direction", "name_family"),
    reach="pair",
)
def _build_derives_within_one_name_beside() -> Predicate:
    return DerivesWithinOneNameBeside()


@declare(
    "derives_from_script_calls",
    version="1",
    summary="Propose derives where one script's calls read the source and write the target",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("describing_formats", "minimum_resolution"),
    support_families=("declared_reference",),
)
def _build_derives_from_script_calls() -> Predicate:
    return DerivesFromScriptCalls()


@declare(
    "derives_from_aggregation",
    version="1",
    summary="Propose derives from many members to one summary over a bounded group",
    effect=RuleEffect.PROPOSES,
    reads=Reads.GROUP,
    parameters=("member_respect", "minimum_members"),
    support_families=("group_membership", "stage_transition"),
)
def _build_derives_from_aggregation() -> Predicate:
    return DerivesFromAggregation()


@declare(
    "equivalent_by_multi_signal_design",
    version="1",
    summary="Propose that two units of work share a design, or differ in one named respect",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("agreeing_kinds", "minimum_support", "requires_repeat_marker", "varying_respect"),
    # A date gap may bound which pairs are considered and may be displayed. It is
    # absent here because two runs of one design usually are close in time, which
    # makes temporal proximity look like the best evidence available and makes it
    # exactly what would group three unrelated efforts from one busy month.
    support_families=("condition_agreement", "name_family", "vocabulary_overlap", "repeat_marker"),
)
def _build_equivalent_by_multi_signal_design() -> Predicate:
    return EquivalentByMultiSignalDesign()


@declare(
    "succeeds_by_ordered_token",
    version="2",
    summary="Propose that the next number in a series of replicates follows the one before it",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("ordering_kind", "series_equivalence", "tolerance_seconds"),
    support_families=("ordered_token",),
    reach="pair",
)
def _build_succeeds_by_ordered_token() -> Predicate:
    return SucceedsByOrderedToken()


@declare(
    "references_by_declared_path",
    version="2",
    summary="Propose that one file points at another, claiming nothing further",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("minimum_resolution",),
    support_families=("declared_reference",),
)
def _build_references_by_declared_path() -> Predicate:
    return ReferencesByDeclaredPath()


@declare(
    "concerns_by_found_sense",
    version="1",
    summary="Propose that two entries are about the same thing on one stated axis",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("minimum_shared_senses", "theme_kind"),
    support_families=("vocabulary_overlap",),
)
def _build_concerns_by_found_sense() -> Predicate:
    return ConcernsByFoundSense()
