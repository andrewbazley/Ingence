"""The Graph page's data: one chosen folder or file and what directly surrounds it.

``GET /api/v1/graph/neighborhood`` and ``GET /api/v1/graph/edge`` are the dashboard guide's first
slice. What is drawn is what the run holds, in three kinds that never blur:

- ``indexed_containment``: a folder and what the census found inside it, read from
  ``entries.parent_entry_id``. Observed, and complete after enumeration.
- ``published_assertion``: an edge the run published, read from ``assertions`` where
  ``decision_status`` is ``asserted``. A withdrawn, blocked or superseded edge is never drawn as
  a current fact.
- ``proposed_candidate``: a pair a rule proposed and nobody has asserted, read from
  ``candidates`` only when the request asks for proposals, labeled proposed, and never promoted by
  being drawn. A candidate the run already promoted is drawn once, as the published edge, which
  names it.

Nothing is invented to make a picture. A deleted entry is never a node. A relation whose other end
is not an entry of the run (an unresolved reference, say) is explained in ``availability``, never
drawn to a made-up node. A ``references`` edge is never drawn as ``derives``: the primitive
travels unchanged from the record to the line.

The route is bounded by construction. The center's neighbors are read by queries keyed on the
center and on the nodes chosen, each with a limit, in a stable order. Nothing loads the run's
entries, assertions or candidates whole. The count of what a cap left out is exact where it could
be computed within :data:`COUNT_BUDGET_SECONDS`, and otherwise ``count_unknown``, never a guess.

A ladder is the chain of levels the Years view climbs: the root, the person (where the first
folder below a root is read as one), the line of work, the experiment, and the file at the foot. A
neighborhood touches many ladders. Its center, its folder, what it contains and what is joined to
it lie in different experiments, and each experiment is a ladder of its own. ``ladders`` lists
every ladder the nodes drawn stand on, the center's first, each with the nodes on it.
``GraphNode.ladder`` names the one a node stands on. The experiment is the engine's one reading
(``experiment_units``: the highest folder above a node that a rule resolved as an experiment's,
none under an installed program), the same whether or not the themes are read. A node in no
experiment stands on the ladder of the first folder below its root, and a folder above an
experiment stands on that experiment's. A ladder's person and line of work come from the themes
projection only where the request asks for them (``lines_of_work=true``). Reading the themes writes
their cache into the project, which the default request does not. A ladder says its line of work
was not asked for, or could not be computed. The nodes and edges are unchanged: the page that draws
them needs nothing from the ladders until it is redrawn.

Every response names the one run it read (``run_id``). That is the run that reads are pinned to
(:attr:`V1Context.run_pinned`): the settled run while a job that will publish a newer one is
running, or a published run the request names. Layout is the page's. Distance on the page is not
evidence, and nothing here computes a position.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import threading
import unicodedata
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

import duckdb
from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.run_names import project_run_names
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import run_named
from ingence.core.relations import PRIMITIVE_PROPERTIES, Primitive
from ingence.core.schema import DecisionStatus, Entry, Root
from ingence.core.tiers import (
    DECISION_TIER_PROPERTIES,
    INFERENCE_TIER_PROPERTIES,
    DecisionTier,
    InferenceTier,
)
from ingence.infer.references import POOL_COLUMNS
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row
from ingence.storage.layout import table_for
from ingence.storage.own_files import filesystem_entry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = [
    "COUNT_BUDGET_SECONDS",
    "DEFAULT_MAX_EDGES",
    "DEFAULT_MAX_NODES",
    "DEFAULT_RELATIONS",
    "LIMITS",
    "MAX_HOPS",
    "EdgeDetail",
    "GraphEdge",
    "GraphNode",
    "NeighborhoodResponse",
    "edge_detail",
    "neighborhood",
]

#: The relations drawn when a request names none: the dashboard guide's first slice.
DEFAULT_RELATIONS: tuple[str, ...] = ("contains", "derives", "references")
#: Hops from the center. The limit is one (the guide: two or three hops come after the first
#: slice is measured). A request for more is refused, not quietly shortened.
MAX_HOPS = 1
#: The caps a request starts from, the guide's figures.
DEFAULT_MAX_NODES = 120
DEFAULT_MAX_EDGES = 240
#: The ranges a request may set them in. Above these a page cannot lay a graph out readably,
#: and a person wanting more wants the relationship list, which pages.
LIMITS: dict[str, tuple[int, int]] = {"max_nodes": (1, 1000), "max_edges": (1, 2000)}
#: How long the exact counts of what a cap left out may take before they are reported as
#: ``count_unknown``. A count is a nicety beside the drawing and never worth a stalled page.
COUNT_BUDGET_SECONDS = 3.0
#: How many of the center's own mentions are resolved again to say what they came to.
MENTION_LIMIT = 2000
#: How many entries a mention's last name may match before the resolution is not attempted:
#: resolving against part of the candidates could make an ambiguous name look unique.
MENTION_POOL_LIMIT = 50_000
#: The columns the pool is read with: only what resolution reads and an entry requires. The resolver
#: reads eight fields of a row, so reading every column would load much that it never uses.
MENTION_POOL_SELECT = ", ".join(
    column
    for column in table_for(Entry).schema.names
    if column in POOL_COLUMNS or column.removesuffix("_json") in POOL_COLUMNS
)
#: How many unresolved or not-drawn relations the explanation lists by name.
UNRESOLVED_LISTED = 20
#: How many ladders a response lists unless a request says fewer, and the range it may ask in.
DEFAULT_MAX_LADDERS = 24
MAX_LADDERS_RANGE = (1, 200)

EdgeKind = Literal["indexed_containment", "published_assertion", "proposed_candidate"]
AvailabilityState = Literal["drawn", "no_matches", "no_published_run"]

_CONTAINMENT = "contains:"
_NODE_COLUMNS = (
    "entry_id, name, relative_path, root_id, entry_type, entry_kind, extension, "
    "scan_outcome, resolution_status, is_deleted, parent_entry_id"
)
_EDGE_COLUMNS = (
    "edge_id, source_id, target_id, primitive, respect, specialization, rule_id, "
    "rule_version, inference_tier, reference_resolution, candidate_id, decision_status"
)
_CANDIDATE_COLUMNS = (
    "candidate_id, source_id, target_id, primitive, respect, specialization, rule_id, "
    "rule_version, decision_tier"
)
#: One line per pair. A candidate is drawn only where no published assertion stands for it: the
#: one it was promoted from, or one any rule published for the same two ends, in the same
#: direction, by the same primitive. Two rules may propose one pair (a name identity and one file
#: at two steps). The published line wins over every candidate for it, and a candidate the other
#: way round, which disagrees with it, is still drawn. The query has one ``?``: the status.
_NOT_PUBLISHED = (
    "NOT EXISTS (SELECT 1 FROM assertions a WHERE a.decision_status = ? AND ("
    "a.candidate_id = c.candidate_id OR (a.source_id = c.source_id "
    "AND a.target_id = c.target_id AND a.primitive = c.primitive)))"
)

#: How a line reads, per primitive: ``{source}`` and ``{target}`` are the endpoints' names.
#: The words follow each primitive's declared definition (``core/relations.py``).
_READINGS: dict[str, str] = {
    "contains": "{source} contains {target}",
    "embeds": "{target} is inside the file {source}",
    "derives": "{target} was produced from {source}",
    "identical": "{source} and {target} hold the same bytes",
    "equivalent": "{source} and {target} are the same with respect to {respect}",
    "succeeds": "{target} follows {source} in a series",
    "references": "{source} mentions or points at {target}",
    "concerns": "{source} and {target} are about the same subject",
}
#: The short label a line carries on the drawing and in the list, read from source to target
#: along the arrow ("kelt_raw.csv source of kelt_processed.csv").
_LABELS: dict[str, str] = {
    "contains": "contains",
    "embeds": "holds inside",
    "derives": "source of",
    "identical": "identical",
    "equivalent": "equivalent",
    "succeeds": "followed by",
    "references": "mentions",
    "concerns": "same subject",
}


# ---------------------------------------------------------------------------
# The response models.
# ---------------------------------------------------------------------------


class GraphNode(ApiModel):
    """One entry on the graph: never a record payload, only what a page shows."""

    id: Identifier
    name: str
    #: The path under its root, as the census recorded it.
    path: str
    root_id: Identifier
    #: ``file``, ``directory``, ``symlink`` or ``other``.
    entry_type: str
    #: ``filesystem``, or ``derived`` for a member promoted from inside a container.
    entry_kind: str
    extension: str
    #: What the last scan did with it (``metadata_only``, ``extracted``, ...).
    scan_outcome: str
    #: Whether its questions are settled: ``unexamined``, ``resolved`` and the two absences.
    resolution_status: str
    is_center: bool
    #: Hops from the center: 0 for the center.
    hop: Count
    #: How it is placed around the center: ``center``, ``parent``, ``child`` or ``related``.
    placement: Literal["center", "parent", "child", "related"]
    #: The ladder it stands on (``ladders``), or None where the ladders cap left that one out.
    ladder: Identifier | None = None


class GraphEdge(ApiModel):
    """One line: its relation, its kind and a concise summary; the detail route has the rest."""

    #: The assertion's ``edge_id``, the candidate's ``candidate_id``, or ``contains:<parent>:
    #: <child>`` for indexed containment.
    id: Identifier
    source: Identifier
    target: Identifier
    #: One of the eight primitives, exactly as recorded.
    primitive: str
    specialization: str | None
    respect: str | None
    kind: EdgeKind
    #: Whether reversing it changes its meaning (the primitive's declared property).
    directed: bool
    symmetric: bool
    #: What the line says on the page: the relation, and ``proposed`` for a candidate.
    label: str
    #: The line in one sentence, with the endpoints' names.
    summary: str
    rule_id: str | None
    rule_version: str | None
    #: The inference tier of an assertion, or the decision tier of a candidate.
    tier: str | None
    #: How a reference resolved, on a ``references`` edge.
    reference_resolution: str | None
    #: A candidate's own identifier, or the candidate a published edge was promoted from.
    candidate_id: Identifier | None


class Truncation(ApiModel):
    """What the caps left out, exactly or said to be unknown; never a guessed number."""

    capped: bool
    max_nodes: Count
    max_edges: Count
    #: Nodes within the requested hops and relations that are not drawn. ``None`` only with
    #: ``count_unknown``.
    nodes_left_out: Count | None
    edges_left_out: Count | None
    #: The exact counts took longer than their budget and were not finished.
    count_unknown: bool
    message: str


class UnresolvedRelation(ApiModel):
    """A published relation of the chosen kinds whose other end is not a live entry."""

    edge_id: Identifier
    primitive: str
    #: The end that is here.
    source: Identifier
    #: What the record names at the other end, as recorded: an identifier no live entry has.
    named: str
    reference_resolution: str | None
    rule_id: str
    rule_version: str
    #: ``not_an_entry`` or ``entry_deleted``.
    reason: Literal["not_an_entry", "entry_deleted"]


class UnresolvedSummary(ApiModel):
    count: Count
    items: list[UnresolvedRelation]


class MentionSummary(ApiModel):
    """What the center's own mentions came to, resolved again by the engine's resolver.

    From the Index alone, by ``infer/references.py``'s rules, so the same run gives the same
    answer. ``by_outcome`` keys are the resolver's outcomes: ``in_folder``, ``rooted``,
    ``from_root``, ``by_prefix``, ``by_name`` (resolved), and ``several_matches`` (ambiguous),
    ``no_match`` (unresolved), ``outside_pool``, ``code_include_not_resolved``, ``not_a_path``.

    A file whose content was not read, or was read only by readers that do not look for paths,
    has no mention recorded, which is not the same as mentioning nothing. Only ``none_found`` says
    a reader that looks for paths read it and found none.
    """

    #: ``resolved_again``: its mentions were resolved again (or counted, where they could not be).
    #: ``none_found``: a reader that looks for paths read it and found none. ``not_read``: its
    #: content was not read in this run. ``reader_does_not_look``: read, but by no reader that
    #: records paths.
    state: Literal["resolved_again", "none_found", "not_read", "reader_does_not_look"]
    #: Whether the center's content was read in this run (``extracted`` or
    #: ``extracted_with_fallback``). Where not, what it mentions is unexamined.
    content_read: bool
    total: Count
    examined: Count
    by_outcome: dict[str, Count]
    #: Why the mentions were not resolved again, or why none is recorded, in words.
    not_resolved_because: str | None
    #: Which of a person's prefix mappings the resolution used: those in the Record up to the
    #: run's watermark, or none where the run records no watermark.
    prefix_mappings: str | None


class NotDrawn(ApiModel):
    """What touches the center and the chosen relations leave out: said, so it is not missed."""

    #: Published edges with the center at one end, by primitive, of relations not chosen.
    published: dict[str, Count]
    #: Proposed pairs with the center at one end that are not drawn: all of them while Proposals
    #: is off, and those of relations not chosen while it is on.
    proposed: Count


class Availability(ApiModel):
    """Why the drawing holds what it holds, and what the engine could not connect."""

    state: AvailabilityState
    message: str
    #: Always false: the graph is read from the project's store, never from the source roots,
    #: so a disconnected drive changes nothing here.
    reads_source: Literal[False] = False
    #: Lines drawn, by kind.
    drawn: dict[str, Count]
    unresolved: UnresolvedSummary
    mentions: MentionSummary | None
    not_drawn: NotDrawn


LadderLevel = Literal["root", "person", "line_of_work", "experiment", "folder"]
LinesOfWork = Literal["placed", "not_placed", "not_computed", "no_experiment"]


class LadderRung(ApiModel):
    """One level of a ladder."""

    level: LadderLevel
    #: The root's, the experiment's or the folder's identifier; the theme's number for a line of
    #: work; None for a person, who is a name and no entry.
    id: Identifier | None
    name: str
    #: The folder's path under its root, for an experiment or a folder; None otherwise.
    path: str | None


class Ladder(ApiModel):
    """One chain of levels, with the nodes of this neighborhood that stand on it."""

    #: ``ladder:<digest>`` of its root and its experiment (or top folder), the same whether or not
    #: the themes were read.
    id: Identifier
    #: Top to bottom: the root, the person and the line of work where the engine knows them, then
    #: the experiment; or, where no folder above is read as an experiment, the first folder below
    #: the root.
    rungs: list[LadderRung]
    #: The nodes drawn that stand on this ladder, in the nodes' own order.
    members: list[Identifier]
    #: Whether the center stands on it.
    is_center_ladder: bool
    #: ``placed``: the experiment is in a line of work. ``not_placed``: it is in none (the reason
    #: says why). ``not_computed``: the themes were not computed (the reason says why, or that the
    #: request left them out). ``no_experiment``: no folder above is read as an experiment.
    line_of_work: LinesOfWork
    line_of_work_reason: str | None


class PinnedRun(ApiModel):
    run_current: Identifier | None
    run_pinned: Identifier | None
    job_running: bool
    #: Whether the run read is the run reads are pinned to (false for an earlier run named).
    is_pinned_run: bool
    message: str


@register("graph_neighborhood")
class NeighborhoodResponse(ApiModel):
    """``GET /api/v1/graph/neighborhood``: one center, its direct neighbors, and the lines.

    Also every ladder the nodes drawn stand on (``ladders``): the root, then the experiment each
    node lies in, or the first folder below the root where no experiment holds it. A ladder's
    person and line of work are read from the themes only where the request says
    ``lines_of_work=true``. Reading them builds the themes and keeps them in the project (the
    cache the Years view writes). So by default this route writes nothing, and a ladder says its
    line of work was not asked for. ``max_ladders`` caps the ladders listed, and ``ladders_total``
    says how many there are.
    """

    schema_version: str
    run_id: Identifier | None
    center_id: Identifier
    hops: Count
    relations: list[str]
    include_candidates: bool
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncation: Truncation
    availability: Availability
    pinned: PinnedRun
    #: Every ladder the nodes drawn stand on, the center's first, at most ``max_ladders``.
    ladders: list[Ladder] = Field(default_factory=list)
    #: How many ladders the nodes drawn stand on in all, and how many the cap left out.
    ladders_total: Count = 0
    ladders_left_out: Count = 0


class EdgeEnd(ApiModel):
    id: Identifier
    name: str | None
    path: str | None
    #: False where the record names something that is not a live entry.
    is_entry: bool


class SupportItem(ApiModel):
    name: str
    family: str
    #: A strength that orders support within one tier. It is not a probability.
    value: float
    observations: list[str]


class DefeaterItem(ApiModel):
    reason: str
    rule_id: str
    rule_version: str
    fired: bool
    observations: list[str]


class ReviewRoute(ApiModel):
    """Where a proposed pair is decided: the existing Proposed pairs page, never a new write."""

    view: Literal["candidates"]
    rule: str
    top: str
    message: str


@register("graph_edge")
class EdgeDetail(ApiModel):
    """``GET /api/v1/graph/edge``: everything one line rests on."""

    schema_version: str
    run_id: Identifier
    id: Identifier
    kind: EdgeKind
    primitive: str
    specialization: str | None
    respect: str | None
    directed: bool
    symmetric: bool
    #: The line in a sentence, with the endpoints' names.
    reading: str
    #: The primitive's declared definition (``core/relations.py``).
    definition: str
    source: EdgeEnd
    target: EdgeEnd | None
    rule_id: str | None
    rule_version: str | None
    #: The rule's statement as the run recorded it, where the run holds the rule.
    rule_statement: str | None
    tier: str | None
    tier_definition: str | None
    #: ``asserted`` for a published edge; ``proposed`` for a candidate; ``observed`` for
    #: indexed containment.
    status: str
    reference_resolution: str | None
    #: The equivalence level the edge lives at (``file`` is the finest).
    level: str | None
    #: ``native`` where asserted at its level, ``lifted`` where lifted from members' edges.
    quotient_origin: str | None
    #: Why expanding a container stopped, on an ``embeds`` edge.
    expansion_state: str | None
    candidate_id: Identifier | None
    support: list[SupportItem]
    defeaters: list[DefeaterItem]
    #: The records the line rests on: the edge or candidate itself, and every observation its
    #: support and defeaters cite.
    records: list[str]
    review: ReviewRoute | None


# ---------------------------------------------------------------------------
# Reading a request.
# ---------------------------------------------------------------------------


def _one(query: Mapping[str, Sequence[str]], name: str) -> str | None:
    values = query.get(name) or []
    return values[0] if values else None


def _integer(
    query: Mapping[str, Sequence[str]], name: str, default: int, low: int, high: int
) -> int:
    text = _one(query, name)
    if text is None or text == "":
        return default
    try:
        value = int(text)
    except ValueError:
        message = f"{name} must be a whole number from {low} to {high}; {text!r} is not one"
        raise ApiError(message, parameter=name) from None
    if not low <= value <= high:
        message = f"{name} must be from {low} to {high}; {value} is outside that"
        raise ApiError(message, parameter=name)
    return value


def _boolean(query: Mapping[str, Sequence[str]], name: str) -> bool:
    text = (_one(query, name) or "false").strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off", ""):
        return False
    message = f"{name} is true or false; {text!r} is neither"
    raise ApiError(message, parameter=name)


def _relations(query: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    text = _one(query, "relations")
    if text is None or not text.strip():
        return DEFAULT_RELATIONS
    asked = [part.strip() for part in text.split(",") if part.strip()]
    known = {primitive.value for primitive in Primitive}
    unknown = [part for part in asked if part not in known]
    if unknown:
        message = (
            f"no relation is called {', '.join(unknown)}; the eight are "
            f"{', '.join(primitive.value for primitive in Primitive)}"
        )
        raise ApiError(message, parameter="relations")
    # Canonical order, so two spellings of one request are one request.
    return tuple(primitive.value for primitive in Primitive if primitive.value in asked)


def _run(context: V1Context, query: Mapping[str, Sequence[str]]) -> tuple[str | None, bool]:
    """The run to read and whether it is the pinned one; only a published run is accepted."""
    return run_named(context, query)


def _run_words(context: V1Context, run_id: str) -> str:
    """A run as a page names it, "Run 5", with no identifier where a name says it.

    The identifier stays in the response's own fields. Reads only the run directories' names.
    """
    try:
        named = project_run_names(context.project).get(run_id)
    except OSError:
        named = None
    return str(named["label"]).split(",")[0] if named else run_id


def _pinned(context: V1Context, run_id: str | None, *, is_pinned: bool) -> PinnedRun:
    if run_id is None:
        message = "No published run yet: nothing to draw."
    elif not is_pinned:
        message = f"An earlier run, {_run_words(context, run_id)}, as asked: not the current one."
    elif context.job_running and context.run_current != run_id:
        message = (
            f"A job is running; this is the settled run, {_run_words(context, run_id)}, until "
            "the new one is published."
        )
    elif context.job_running:
        message = (
            f"A job is running; this is the settled run, {_run_words(context, run_id)}, until "
            "it publishes."
        )
    else:
        message = f"The current run, {_run_words(context, run_id)}."
    return PinnedRun(
        run_current=context.run_current,
        run_pinned=context.run_pinned,
        job_running=context.job_running,
        is_pinned_run=is_pinned,
        message=message,
    )


# ---------------------------------------------------------------------------
# Rows to models.
# ---------------------------------------------------------------------------


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _node(row: Mapping[str, Any], *, hop: int, placement: str) -> GraphNode:
    return GraphNode(
        id=str(row["entry_id"]),
        name=str(row["name"] or row["relative_path"] or row["entry_id"]),
        path=str(row["relative_path"] or ""),
        root_id=str(row["root_id"]),
        entry_type=str(row["entry_type"]),
        entry_kind=str(row["entry_kind"]),
        extension=str(row["extension"] or ""),
        scan_outcome=str(row["scan_outcome"]),
        resolution_status=str(row["resolution_status"]),
        is_center=placement == "center",
        hop=hop,
        placement=placement,
    )


def _reading(primitive: str, source: str, target: str, respect: str | None) -> str:
    template = _READINGS.get(primitive, "{source} " + primitive + " {target}")
    return template.format(source=source, target=target, respect=respect or "a stated respect")


def _containment_edge(parent: GraphNode, child: GraphNode) -> GraphEdge:
    properties = PRIMITIVE_PROPERTIES[Primitive.CONTAINS]
    return GraphEdge(
        id=f"{_CONTAINMENT}{parent.id}:{child.id}",
        source=parent.id,
        target=child.id,
        primitive=Primitive.CONTAINS.value,
        specialization="directory_of",
        respect=None,
        kind="indexed_containment",
        directed=properties.directed,
        symmetric=properties.symmetric,
        label="contains",
        summary=f"Indexed directory: {child.name} is inside {parent.name}.",
        rule_id=None,
        rule_version=None,
        tier=InferenceTier.ENTAILED.value,
        reference_resolution=None,
        candidate_id=None,
    )


def _relation_edge(
    row: Mapping[str, Any], names: Mapping[str, str], *, proposed: bool
) -> GraphEdge:
    primitive = str(row["primitive"])
    properties = PRIMITIVE_PROPERTIES[Primitive(primitive)]
    source, target = str(row["source_id"]), str(row["target_id"])
    reading = _reading(
        primitive, names.get(source, source), names.get(target, target), row["respect"]
    )
    label = _LABELS.get(primitive, primitive)
    if proposed:
        tier = str(row["decision_tier"])
        return GraphEdge(
            id=str(row["candidate_id"]),
            source=source,
            target=target,
            primitive=primitive,
            specialization=row["specialization"],
            respect=row["respect"],
            kind="proposed_candidate",
            directed=properties.directed,
            symmetric=properties.symmetric,
            label=f"{label} (proposed)",
            summary=f"Proposed, not asserted: {reading} ({tier} evidence).",
            rule_id=str(row["rule_id"]),
            rule_version=str(row["rule_version"]),
            tier=tier,
            reference_resolution=None,
            candidate_id=str(row["candidate_id"]),
        )
    resolution = row["reference_resolution"]
    said = f"Published: {reading}"
    if resolution:
        said += f" (the reference resolved {str(resolution).replace('_', ' ')})"
    return GraphEdge(
        id=str(row["edge_id"]),
        source=source,
        target=target,
        primitive=primitive,
        specialization=row["specialization"],
        respect=row["respect"],
        kind="published_assertion",
        directed=properties.directed,
        symmetric=properties.symmetric,
        label=label,
        summary=said + ".",
        rule_id=str(row["rule_id"]),
        rule_version=str(row["rule_version"]),
        tier=str(row["inference_tier"]),
        reference_resolution=str(resolution) if resolution else None,
        candidate_id=str(row["candidate_id"]) if row["candidate_id"] else None,
    )


# ---------------------------------------------------------------------------
# The neighborhood.
# ---------------------------------------------------------------------------


@dataclass
class _Budget:
    """Interrupts a query that outlives its budget, so a count can be said to be unknown."""

    catalog: Catalog
    seconds: float
    timer: threading.Timer | None = field(default=None)
    fired: bool = False

    def __enter__(self) -> _Budget:
        def interrupt() -> None:
            self.fired = True
            self.catalog.connection.interrupt()

        self.timer = threading.Timer(self.seconds, interrupt)
        self.timer.daemon = True
        self.timer.start()
        return self

    def __exit__(self, *_: object) -> None:
        if self.timer is not None:
            self.timer.cancel()


def _in(values: Sequence[str]) -> str:
    """A run of placeholders for an ``IN`` list; every value is bound, never spelled in SQL."""
    return ", ".join("?" for _ in values) or "NULL"


class _Reader:
    """The bounded queries one neighborhood needs, each keyed on the center or the nodes."""

    def __init__(self, catalog: Catalog, relations: tuple[str, ...], *, candidates: bool) -> None:
        self.catalog = catalog
        self.containment = Primitive.CONTAINS.value in relations
        #: The relations read from ``assertions`` and ``candidates``. Containment is the
        #: census's, from ``entries``: its ``contains`` assertions say the same thing twice.
        self.published = tuple(name for name in relations if name != Primitive.CONTAINS.value)
        self.candidates = candidates

    def entry(self, entry_id: str) -> dict[str, Any] | None:
        found = _rows(
            self.catalog.query(
                f"SELECT {_NODE_COLUMNS} FROM entries WHERE entry_id = ? LIMIT 1",  # noqa: S608
                [entry_id],
            )
        )
        return found[0] if found else None

    def entries(self, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        rows = _rows(
            self.catalog.query(
                f"SELECT {_NODE_COLUMNS} FROM entries WHERE entry_id IN ({_in(ids)})",  # noqa: S608
                list(ids),
            )
        )
        return {str(row["entry_id"]): row for row in rows}

    def children(self, parent: str, limit: int) -> list[dict[str, Any]]:
        if not self.containment:
            return []
        return _rows(
            self.catalog.query(
                f"SELECT {_NODE_COLUMNS} FROM entries WHERE parent_entry_id = ? "  # noqa: S608
                "AND NOT is_deleted AND entry_kind = 'filesystem' "
                "ORDER BY name, entry_id LIMIT ?",
                [parent, limit],
            )
        )

    def touching(self, center: str, limit: int) -> list[dict[str, Any]]:
        """Published edges of the chosen relations with the center at one end."""
        if not self.published:
            return []
        return _rows(
            self.catalog.query(
                f"SELECT {_EDGE_COLUMNS} FROM assertions WHERE decision_status = ? "  # noqa: S608
                f"AND primitive IN ({_in(self.published)}) AND (source_id = ? OR target_id = ?) "
                "ORDER BY primitive, edge_id LIMIT ?",
                [DecisionStatus.ASSERTED.value, *self.published, center, center, limit],
            )
        )

    def proposed(self, center: str, limit: int) -> list[dict[str, Any]]:
        """Candidates with the center at one end, leaving out any the run promoted."""
        if not self.candidates or not self.published:
            return []
        return _rows(
            self.catalog.query(
                f"SELECT {_CANDIDATE_COLUMNS} FROM candidates c "  # noqa: S608
                f"WHERE c.primitive IN ({_in(self.published)}) "
                "AND (c.source_id = ? OR c.target_id = ?) "
                f"AND {_NOT_PUBLISHED} ORDER BY c.primitive, c.candidate_id LIMIT ?",
                [*self.published, center, center, DecisionStatus.ASSERTED.value, limit],
            )
        )

    def hold(self, ids: Sequence[str]) -> None:
        """Keep the drawn nodes as a temporary table of this request's own connection.

        The queries about edges among the drawn nodes join it. Binding the identifiers as
        parameters to each of them cost a dense folder about a tenth of a second per query on a
        real project's clone, against a few milliseconds for the join (measured); the table
        lives only as long as this request's in-memory connection.
        """
        self.catalog.query(
            "CREATE OR REPLACE TEMP TABLE drawn_nodes AS SELECT unnest(?::VARCHAR[]) AS id",
            [list(ids)],
        )

    def among(self, center: str, limit: int) -> list[dict[str, Any]]:
        """Published edges between two drawn nodes neither of which is the center."""
        if not self.published:
            return []
        return _rows(
            self.catalog.query(
                f"SELECT {_EDGE_COLUMNS} FROM assertions WHERE decision_status = ? "  # noqa: S608
                f"AND primitive IN ({_in(self.published)}) "
                "AND source_id IN (SELECT id FROM drawn_nodes) "
                "AND target_id IN (SELECT id FROM drawn_nodes) "
                "AND source_id <> ? AND target_id <> ? ORDER BY primitive, edge_id LIMIT ?",
                [DecisionStatus.ASSERTED.value, *self.published, center, center, limit],
            )
        )

    def proposed_among(self, center: str, limit: int) -> list[dict[str, Any]]:
        if not self.candidates or not self.published:
            return []
        return _rows(
            self.catalog.query(
                f"SELECT {_CANDIDATE_COLUMNS} FROM candidates c "  # noqa: S608
                f"WHERE c.primitive IN ({_in(self.published)}) "
                "AND c.source_id IN (SELECT id FROM drawn_nodes) "
                "AND c.target_id IN (SELECT id FROM drawn_nodes) "
                "AND c.source_id <> ? AND c.target_id <> ? "
                f"AND {_NOT_PUBLISHED} ORDER BY c.primitive, c.candidate_id LIMIT ?",
                [*self.published, center, center, DecisionStatus.ASSERTED.value, limit],
            )
        )

    def _hood_sql(self, centre_row: Mapping[str, Any]) -> tuple[str, list[Any]]:
        """Every live entry within one hop through the chosen relations, as a SQL relation."""
        parts: list[str] = ["SELECT ? AS id"]
        parameters: list[Any] = [centre_row["entry_id"]]
        if self.containment:
            parts.append(
                "SELECT entry_id FROM entries WHERE parent_entry_id = ? "
                "AND entry_kind = 'filesystem'"
            )
            parameters.append(centre_row["entry_id"])
            if centre_row["parent_entry_id"] and centre_row["entry_kind"] == "filesystem":
                parts.append("SELECT ?")
                parameters.append(centre_row["parent_entry_id"])
        if self.published:
            parts.append(
                "SELECT CASE WHEN source_id = ? THEN target_id ELSE source_id END FROM assertions "  # noqa: S608
                f"WHERE decision_status = ? AND primitive IN ({_in(self.published)}) "
                "AND (source_id = ? OR target_id = ?)"
            )
            parameters += [
                centre_row["entry_id"],
                DecisionStatus.ASSERTED.value,
                *self.published,
                centre_row["entry_id"],
                centre_row["entry_id"],
            ]
            if self.candidates:
                parts.append(
                    "SELECT CASE WHEN source_id = ? THEN target_id ELSE source_id END "  # noqa: S608
                    f"FROM candidates c WHERE c.primitive IN ({_in(self.published)}) "
                    f"AND (c.source_id = ? OR c.target_id = ?) AND {_NOT_PUBLISHED}"
                )
                parameters += [
                    centre_row["entry_id"],
                    *self.published,
                    centre_row["entry_id"],
                    centre_row["entry_id"],
                    DecisionStatus.ASSERTED.value,
                ]
        union = " UNION ".join(parts)
        sql = (
            f"SELECT e.entry_id, e.parent_entry_id, e.entry_kind FROM ({union}) n "  # noqa: S608
            "JOIN entries e ON e.entry_id = n.id AND NOT e.is_deleted"
        )
        return sql, parameters

    def totals(self, centre_row: Mapping[str, Any]) -> tuple[int, int] | None:
        """The exact number of nodes and lines within one hop, or None past the budget."""
        hood, parameters = self._hood_sql(centre_row)
        counted: list[str] = []
        values: list[Any] = [*parameters]
        if self.containment:
            counted.append(
                "SELECT count(*) FROM hood c JOIN hood p ON c.parent_entry_id = p.entry_id "
                "WHERE c.entry_kind = 'filesystem'"
            )
        if self.published:
            counted.append(
                "SELECT count(*) FROM assertions a WHERE a.decision_status = ? "  # noqa: S608
                f"AND a.primitive IN ({_in(self.published)}) "
                "AND a.source_id IN (SELECT entry_id FROM hood) "
                "AND a.target_id IN (SELECT entry_id FROM hood)"
            )
            values += [DecisionStatus.ASSERTED.value, *self.published]
            if self.candidates:
                counted.append(
                    "SELECT count(*) FROM candidates c "  # noqa: S608
                    f"WHERE c.primitive IN ({_in(self.published)}) "
                    "AND c.source_id IN (SELECT entry_id FROM hood) "
                    f"AND c.target_id IN (SELECT entry_id FROM hood) AND {_NOT_PUBLISHED}"
                )
                values += [*self.published, DecisionStatus.ASSERTED.value]
        edges_sql = " + ".join(f"({part})" for part in counted) or "0"
        sql = f"WITH hood AS ({hood}) SELECT (SELECT count(*) FROM hood), {edges_sql}"  # noqa: S608
        try:
            with _Budget(self.catalog, COUNT_BUDGET_SECONDS) as budget:
                row = self.catalog.query(sql, values).fetchone()
        except duckdb.InterruptException:
            return None
        if budget.fired or row is None:
            return None
        return int(row[0]), int(row[1])

    def not_drawn(self, center: str, relations: Sequence[str]) -> NotDrawn:
        """What touches the center that the chosen relations or the Proposals switch leave out."""
        left_out = [
            primitive.value
            for primitive in Primitive
            if primitive.value not in relations and primitive is not Primitive.CONTAINS
        ]
        published: dict[str, int] = {}
        if left_out:
            for primitive, count in self.catalog.query(
                "SELECT primitive, count(*) FROM assertions WHERE decision_status = ? "  # noqa: S608
                f"AND primitive IN ({_in(left_out)}) AND (source_id = ? OR target_id = ?) "
                "GROUP BY primitive ORDER BY primitive",
                [DecisionStatus.ASSERTED.value, *left_out, center, center],
            ).fetchall():
                published[str(primitive)] = int(count)
        hidden = [primitive.value for primitive in Primitive] if not self.candidates else left_out
        proposed = 0
        if hidden:
            row = self.catalog.query(
                f"SELECT count(*) FROM candidates c WHERE c.primitive IN ({_in(hidden)}) "  # noqa: S608
                "AND (c.source_id = ? OR c.target_id = ?) "
                f"AND {_NOT_PUBLISHED}",
                [*hidden, center, center, DecisionStatus.ASSERTED.value],
            ).fetchone()
            proposed = int(row[0]) if row else 0
        return NotDrawn(published=published, proposed=proposed)

    def unresolved(self) -> tuple[int, list[dict[str, Any]]]:
        """Published relations touching a drawn node whose other end is no live entry.

        The edges touching the drawn nodes are found first, and only their ends are looked up
        in ``entries``.
        """
        if not self.published:
            return 0, []
        where = (
            "FROM (SELECT * FROM assertions WHERE decision_status = ? "  # noqa: S608
            f"AND primitive IN ({_in(self.published)}) "
            "AND (source_id IN (SELECT id FROM drawn_nodes) "
            "OR target_id IN (SELECT id FROM drawn_nodes))) a "
            "LEFT JOIN entries s ON s.entry_id = a.source_id "
            "LEFT JOIN entries t ON t.entry_id = a.target_id "
            "WHERE (s.entry_id IS NULL OR t.entry_id IS NULL OR s.is_deleted OR t.is_deleted)"
        )
        parameters = [DecisionStatus.ASSERTED.value, *self.published]
        row = self.catalog.query(f"SELECT count(*) {where}", parameters).fetchone()
        listed = _rows(
            self.catalog.query(
                "SELECT a.edge_id, a.primitive, a.source_id, a.target_id, "
                "a.reference_resolution, a.rule_id, a.rule_version, "
                "s.entry_id IS NOT NULL AND NOT s.is_deleted AS source_live, "
                "t.entry_id IS NULL AS target_missing, s.entry_id IS NULL AS source_missing "
                f"{where} ORDER BY a.edge_id LIMIT ?",
                [*parameters, UNRESOLVED_LISTED],
            )
        )
        return (int(row[0]) if row else 0), listed


def _unresolved_item(row: Mapping[str, Any]) -> UnresolvedRelation:
    source_live = bool(row["source_live"])
    here, named = (
        (str(row["source_id"]), str(row["target_id"]))
        if source_live
        else (str(row["target_id"]), str(row["source_id"]))
    )
    missing = bool(row["target_missing"]) if source_live else bool(row["source_missing"])
    return UnresolvedRelation(
        edge_id=str(row["edge_id"]),
        primitive=str(row["primitive"]),
        source=here,
        named=named,
        reference_resolution=row["reference_resolution"],
        rule_id=str(row["rule_id"]),
        rule_version=str(row["rule_version"]),
        reason="not_an_entry" if missing else "entry_deleted",
    )


#: The scan outcomes whose content was read. Every other outcome (metadata only, deferred,
#: skipped by policy, a content error, changed during the read) leaves the content unexamined.
_CONTENT_READ = frozenset({"extracted", "extracted_with_fallback"})


def _prefix_mappings(project: Path, watermark: int | None) -> tuple[tuple[Any, ...], str]:
    """A person's prefix mappings as the Record held them at the run's watermark, and in words.

    The run was resolved against the Record as it stood then, so a mapping written since is
    not applied to it. A run that records no watermark gets no mappings. An older run made before
    runs recorded one has none, and so does a census made with ``dictionary.tokenise_names`` off.
    What the Record said when such a run was made cannot be told, and a mapping written later must
    not be read into it.
    """
    from ingence.infer.references import prefix_mappings_in_force
    from ingence.record.log import read_at

    if watermark is None:
        return (), "none applied: this run records no Record watermark"
    # Read as the run read them: the run's own reader, at its watermark, so a withdrawal written
    # since does not reach back on its page.
    with read_at(project, watermark):
        found = prefix_mappings_in_force(project)
    return found, f"{len(found)} from the Record up to event {watermark}"


def _watermark(catalog: Catalog) -> int | None:
    try:
        row = catalog.query("SELECT record_watermark FROM run LIMIT 1").fetchone()
    except duckdb.Error:  # a run written before the column existed
        return None
    return int(row[0]) if row is not None and row[0] is not None else None


def _mentions(catalog: Catalog, project: Path, center: Mapping[str, Any]) -> MentionSummary | None:
    """The center's own mentions, resolved again from the Index by the engine's resolver.

    The pool handed to the resolver holds only the entries a mention could name (those whose
    name is a mention's last name) and the center with its container. This is every entry
    ``infer/references.py`` can answer with. The rest of the Index cannot change the answer.
    """
    from ingence.infer.references import REFERENCES_KEY, PoolPaths, mentions_of, resolve_all
    from ingence.scan.mention_shapes import SHAPES_KEY

    if center["entry_type"] == "directory":
        return None
    outcome = str(center["scan_outcome"])
    content_read = outcome in _CONTENT_READ
    rows = catalog.query(
        "SELECT observation_id, entry_id, observer, key, value_structure FROM observations "
        "WHERE entry_id = ? AND key IN (?, ?) ORDER BY observation_id",
        [center["entry_id"], REFERENCES_KEY, SHAPES_KEY],
    ).fetchall()
    mentions = mentions_of(rows)
    if not mentions:
        if not content_read:
            return MentionSummary(
                state="not_read",
                content_read=False,
                total=0,
                examined=0,
                by_outcome={},
                not_resolved_because=(
                    f"no mention recorded: its contents were not read in this run "
                    f"({outcome.replace('_', ' ')}), so what it mentions is unexamined"
                ),
                prefix_mappings=None,
            )
        # A reader that looks for paths is one this run saw record a path; only such a reader
        # finding none says the file mentions none.
        readers = sorted(
            str(row[0])
            for row in catalog.query(
                "SELECT DISTINCT observer FROM observations WHERE entry_id = ?",
                [center["entry_id"]],
            ).fetchall()
        )
        looking = (
            catalog.query(
                f"SELECT 1 FROM observations WHERE key = ? AND observer IN ({_in(readers)}) "  # noqa: S608
                "LIMIT 1",
                [REFERENCES_KEY, *readers],
            ).fetchone()
            if readers
            else None
        )
        if looking is None:
            return MentionSummary(
                state="reader_does_not_look",
                content_read=True,
                total=0,
                examined=0,
                by_outcome={},
                not_resolved_because=(
                    "no mention recorded: the readers that read it ("
                    + (", ".join(readers) or "none")
                    + ") do not look for paths, so what it mentions is unexamined"
                ),
                prefix_mappings=None,
            )
        return MentionSummary(
            state="none_found",
            content_read=True,
            total=0,
            examined=0,
            by_outcome={},
            not_resolved_because=None,
            prefix_mappings=None,
        )
    examined = mentions[:MENTION_LIMIT]
    lasts = sorted(
        {
            unicodedata.normalize("NFC", mention.path)
            .replace("\\", "/")
            .rstrip("/")
            .rsplit("/", 1)[-1]
            .lower()
            for mention in examined
        }
    )
    mappings, mapped = _prefix_mappings(project, _watermark(catalog))
    matching = "NOT is_deleted AND list_contains(?, lower(name))"

    def unresolved(reason: str) -> MentionSummary:
        return MentionSummary(
            state="resolved_again",
            content_read=content_read,
            total=len(mentions),
            examined=0,
            by_outcome={},
            not_resolved_because=reason,
            prefix_mappings=mapped,
        )

    related = [str(center["entry_id"])]
    if center["parent_entry_id"]:
        related.append(str(center["parent_entry_id"]))
    beneath = center["entry_kind"] == "derived" and bool(center["parent_entry_id"])
    try:
        with _Budget(catalog, COUNT_BUDGET_SECONDS) as budget:
            counted = catalog.query(
                f"SELECT count(*) FROM entries WHERE {matching}",  # noqa: S608
                [lasts],
            ).fetchone()
            if counted is not None and int(counted[0]) > MENTION_POOL_LIMIT:
                return unresolved(
                    f"its mentions' names match more than {MENTION_POOL_LIMIT:,} items, too "
                    "many to resolve again here; the run's own tally counts them"
                )
            entry_rows = _rows(
                catalog.query(
                    f"SELECT {MENTION_POOL_SELECT} FROM entries WHERE ({matching}) "  # noqa: S608
                    f"OR entry_id IN ({_in(related)}) "
                    + ("OR (parent_entry_id = ? AND NOT is_deleted)" if beneath else ""),
                    [lasts, *related, *([center["parent_entry_id"]] if beneath else [])],
                )
            )
    except duckdb.InterruptException:
        return unresolved("resolving its mentions again did not finish in time")
    if budget.fired:
        return unresolved("resolving its mentions again did not finish in time")
    roots = {
        str(row["root_id"]): record_from_row(row, Root)
        for row in _rows(catalog.query("SELECT * FROM roots ORDER BY root_id"))
    }
    pool = PoolPaths.of((record_from_row(row, Entry) for row in entry_rows), roots, mappings)
    outcomes: dict[str, int] = {}
    for resolution in resolve_all(examined, pool):
        outcomes[resolution.outcome] = outcomes.get(resolution.outcome, 0) + 1
    return MentionSummary(
        state="resolved_again",
        content_read=content_read,
        total=len(mentions),
        examined=len(examined),
        by_outcome=dict(sorted(outcomes.items())),
        not_resolved_because=None
        if len(examined) == len(mentions)
        else f"only the first {MENTION_LIMIT:,} of its mentions were resolved again here",
        prefix_mappings=mapped,
    )


@dataclass
class _Foot:
    """Where one node stands: the experiment at or above it, or the first folder below its root."""

    key: str
    unit: Mapping[str, Any] | None
    #: The first folder below the root on its chain; None where there is none.
    top: Mapping[str, Any] | None
    #: The root's own directory entry, where its chain reaches it.
    root: Mapping[str, Any] | None


def _chains(catalog: Catalog) -> dict[str, list[dict[str, Any]]]:
    """Each drawn node and the folders above it, up to the top: ``{id: [itself, parent, ...]}``.

    One recursive query over the nodes held as ``drawn_nodes``; a chain is as long as its folder
    tree is deep, and no node's chain is read twice.
    """
    rows = _rows(
        catalog.query(
            "WITH RECURSIVE up(start_id, entry_id, parent_id, steps) AS ("
            "SELECT entry_id, entry_id, parent_entry_id, 0 FROM entries "
            "WHERE entry_id IN (SELECT id FROM drawn_nodes) "
            "UNION ALL SELECT u.start_id, e.entry_id, e.parent_entry_id, u.steps + 1 FROM up u "
            "JOIN entries e ON e.entry_id = u.parent_id) "
            "SELECT u.start_id, u.steps, e.entry_id, e.name, e.relative_path, e.entry_type, "
            "e.root_id, e.depth, e.parent_entry_id FROM up u "
            "JOIN entries e ON e.entry_id = u.entry_id ORDER BY u.start_id, u.steps"
        )
    )
    chains: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        chains.setdefault(str(row["start_id"]), []).append(row)
    return chains


def _units(
    catalog: Catalog, chains: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, Mapping[str, Any]]:
    """Each drawn node's experiment, by the engine's one definition (``experiment_units``): the
    highest folder above it that a rule resolved as an experiment's, or itself, and none under an
    installed program. Nodes under no experiment are left out.

    The reading is the same whether or not the themes are asked for, so ``lines_of_work`` only adds
    the person and the line of work to a ladder and never moves a node to another one. It is the
    definition completeness and the themes share, applied to the chains drawn rather than to every
    folder of the run.
    """
    from ingence.experiment_units import counted_folders, highest_experiment, program_folders

    parents: dict[str, str | None] = {}
    rows: dict[str, Mapping[str, Any]] = {}
    for chain in chains.values():
        for held in chain:
            entry = str(held["entry_id"])
            parent = held["parent_entry_id"]
            parents[entry] = None if parent is None else str(parent)
            rows[entry] = held
    programs = program_folders(catalog, parents)
    experiments = (counted_folders(catalog) & frozenset(parents)) - programs
    found = highest_experiment(parents, experiments)
    return {
        entry: rows[unit] for entry, unit in found.items() if entry not in programs and unit in rows
    }


def _ladder_id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"ladder:{digest}"


def _ladders(
    catalog: Catalog,
    context: V1Context,
    run_id: str,
    nodes: Sequence[GraphNode],
    *,
    center: str,
    max_ladders: int,
    lines_of_work: bool,
) -> tuple[list[Ladder], int, dict[str, str]]:
    """Every ladder the drawn nodes stand on: the ladders, how many there are in all, and each
    node's ladder (a node on a ladder the cap left out has none).

    A node stands on the ladder of the experiment at or above it. One in no experiment stands on
    the ladder of the first folder below its root (the root's own where it lies directly in it).
    A folder that holds another drawn node, and that no experiment holds, stands on that node's
    ladder: a folder above an experiment is a rung of its chain and no ladder of its own.
    """
    chains = _chains(catalog)
    units = _units(catalog, chains)
    # The themes only add rungs: where they were asked for and computed, an experiment that
    # the themes draw gains its person and its line of work. No node changes ladder for it.
    places: dict[str, dict[str, Any]] | None = None
    reason: str | None = None
    if not lines_of_work:
        reason = "lines of work were not asked for (lines_of_work=true asks for them)"
    elif units:
        from ingence.themes import experiment_places_or_reason

        places, reason = experiment_places_or_reason(context.project, run_id)
    feet: dict[str, _Foot] = {}
    for node in nodes:
        chain = chains.get(node.id, [])
        if not chain:
            continue
        unit = units.get(node.id)
        # The root's own directory is the chain's last entry (depth 0). The first folder below it is
        # the one before.
        root_entry = chain[-1] if int(chain[-1]["depth"] or 0) == 0 else None
        below = chain[-2] if root_entry is not None and len(chain) > 1 else None
        top = below if below is not None and below["entry_type"] == "directory" else None
        if unit is not None:
            feet[node.id] = _Foot(str(unit["entry_id"]), unit, top, root_entry)
        elif top is not None:
            feet[node.id] = _Foot(str(top["entry_id"]), None, top, root_entry)
        else:
            feet[node.id] = _Foot(f"root:{chain[-1]['root_id']}", None, None, root_entry)
    above = {node_id: {str(held["entry_id"]) for held in chains[node_id]} for node_id in feet}
    for node in nodes:
        foot = feet.get(node.id)
        if foot is None or foot.unit is not None or node.entry_type != "directory":
            continue
        holder = next(
            (
                other.id
                for other in nodes
                if other.id != node.id and other.id in feet and node.id in above[other.id]
            ),
            None,
        )
        if holder is not None:
            feet[node.id] = feet[holder]

    labels = {
        str(root_id): label
        for root_id, label in catalog.query(
            "SELECT root_id, label FROM roots WHERE root_id IN "
            "(SELECT DISTINCT root_id FROM entries WHERE entry_id IN (SELECT id FROM drawn_nodes))"
        ).fetchall()
    }
    by_key: dict[str, list[str]] = {}
    for node in nodes:
        if node.id in feet:
            by_key.setdefault(feet[node.id].key, []).append(node.id)
    built: list[Ladder] = []
    for key, members in by_key.items():
        foot = feet[members[0]]
        anchor = foot.unit or foot.top
        root_id = str(anchor["root_id"]) if anchor is not None else key.removeprefix("root:")
        # The root is named as the project named it, else by its directory's own name: never by
        # where it is on this machine.
        root_name = labels.get(root_id) or (foot.root["name"] if foot.root is not None else None)
        rungs = [LadderRung(level="root", id=root_id, name=str(root_name or "the root"), path=None)]
        state: LinesOfWork = "no_experiment"
        why: str | None = None
        if foot.unit is None:
            if foot.top is not None:
                rungs.append(
                    LadderRung(
                        level="folder",
                        id=str(foot.top["entry_id"]),
                        name=str(foot.top["name"]),
                        path=str(foot.top["relative_path"]),
                    )
                )
            why = "no folder above it is read as an experiment"
        else:
            place = places.get(key) if places is not None else None
            if places is None:
                state, why = "not_computed", reason
            elif place is None:
                state = "not_placed"
                why = "the themes do not draw this folder as an experiment of its own"
            elif place["theme_id"] is None:
                state = "not_placed"
                why = place.get("unplaced_reason") or "it is in no line of work"
            else:
                state = "placed"
            if place is not None and place.get("person"):
                rungs.append(
                    LadderRung(level="person", id=None, name=str(place["person"]), path=None)
                )
            if state == "placed" and place is not None:
                rungs.append(
                    LadderRung(
                        level="line_of_work",
                        id=str(place["theme_id"]),
                        name=str(place["label"] or place["theme_id"]),
                        path=None,
                    )
                )
            rungs.append(
                LadderRung(
                    level="experiment",
                    id=key,
                    name=str(foot.unit["name"]),
                    path=str(foot.unit["relative_path"]),
                )
            )
        built.append(
            Ladder(
                id=_ladder_id(root_id, key),
                rungs=rungs,
                members=members,
                is_center_ladder=center in members,
                line_of_work=state,
                line_of_work_reason=why,
            )
        )
    built.sort(
        key=lambda ladder: (
            not ladder.is_center_ladder,
            -len(ladder.members),
            ladder.rungs[-1].path or "",
            ladder.id,
        )
    )
    kept = built[:max_ladders]
    standing = {member: ladder.id for ladder in kept for member in ladder.members}
    return kept, len(built), standing


def _left_out_message(nodes: int | None, edges: int | None, *, capped: bool) -> str:
    if nodes is None or edges is None:
        return (
            "Caps reached; how many were left out could not be counted in time, so no number "
            "is given."
            if capped
            else "Everything within the caps is drawn; the full count was not finished in time."
        )
    if not nodes and not edges:
        return "Everything one step out with these relations is drawn."
    said = []
    if nodes:
        said.append(f"{nodes:,} item{'' if nodes == 1 else 's'}")
    if edges:
        said.append(f"{edges:,} line{'' if edges == 1 else 's'}")
    return f"Left out by the caps: {' and '.join(said)}."


def _empty(
    context: V1Context,
    center: str,
    relations: tuple[str, ...],
    *,
    candidates: bool,
    max_nodes: int,
    max_edges: int,
) -> NeighborhoodResponse:
    return NeighborhoodResponse(
        schema_version=SCHEMA_VERSION,
        run_id=None,
        center_id=center,
        hops=1,
        relations=list(relations),
        include_candidates=candidates,
        nodes=[],
        edges=[],
        truncation=Truncation(
            capped=False,
            max_nodes=max_nodes,
            max_edges=max_edges,
            nodes_left_out=0,
            edges_left_out=0,
            count_unknown=False,
            message="Nothing to draw.",
        ),
        availability=Availability(
            state="no_published_run",
            message="This project has no published run yet: index it, and the graph reads that.",
            drawn={},
            unresolved=UnresolvedSummary(count=0, items=[]),
            mentions=None,
            not_drawn=NotDrawn(published={}, proposed=0),
        ),
        pinned=_pinned(context, None, is_pinned=True),
    )


def neighborhood(context: V1Context, query: Mapping[str, Sequence[str]]) -> NeighborhoodResponse:
    """One center and its direct neighbors, bounded, in a stable order (see the module)."""
    centre_id = (_one(query, "center") or "").strip()
    if not centre_id:
        message = "choose a folder or file: center names the item the graph starts from"
        raise ApiError(message, parameter="center")
    hops = _integer(query, "hops", 1, 1, MAX_HOPS)
    relations = _relations(query)
    candidates = _boolean(query, "include_candidates")
    max_nodes = _integer(query, "max_nodes", DEFAULT_MAX_NODES, *LIMITS["max_nodes"])
    max_edges = _integer(query, "max_edges", DEFAULT_MAX_EDGES, *LIMITS["max_edges"])
    max_ladders = _integer(query, "max_ladders", DEFAULT_MAX_LADDERS, *MAX_LADDERS_RANGE)
    lines_of_work = _boolean(query, "lines_of_work")
    run_id, is_pinned = _run(context, query)
    if run_id is None:
        return _empty(
            context,
            centre_id,
            relations,
            candidates=candidates,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    with open_catalog(context.project, run_id=run_id) as catalog:
        reader = _Reader(catalog, relations, candidates=candidates)
        centre_row = reader.entry(centre_id)
        if centre_row is None:
            message = f"run {run_id} holds no item {centre_id}"
            raise ApiError(
                message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="center"
            )
        if centre_row["is_deleted"]:
            message = (
                f"{centre_row['relative_path'] or centre_id} was deleted before run {run_id}; "
                "a deleted item is never drawn"
            )
            raise ApiError(
                message, code="entry_deleted", status=HTTPStatus.NOT_FOUND, parameter="center"
            )
        center = _node(centre_row, hop=0, placement="center")

        # The nodes, in the order they are kept when a cap bites: the center, its folder, what
        # a published relation joins it to, what it contains, and what a proposal names.
        ordered: dict[str, GraphNode] = {center.id: center}
        rows_by_id: dict[str, Mapping[str, Any]] = {center.id: centre_row}
        touching = reader.touching(center.id, max_edges + 1)
        proposed = reader.proposed(center.id, max_edges + 1)
        other_ends = [
            str(row["target_id"] if row["source_id"] == center.id else row["source_id"])
            for row in (*touching, *proposed)
        ]
        wanted = list(dict.fromkeys(other_ends))
        parent_id = (
            str(centre_row["parent_entry_id"])
            if reader.containment
            and centre_row["parent_entry_id"]
            and centre_row["entry_kind"] == "filesystem"
            else None
        )
        found = reader.entries([*([parent_id] if parent_id else []), *wanted])
        live = {key: row for key, row in found.items() if not row["is_deleted"]}

        # Whether a cap left something out that the queries saw: the max+1 probe.
        overflow = {"nodes": False, "edges": False}

        def keep(row: Mapping[str, Any], placement: str) -> None:
            identifier = str(row["entry_id"])
            if identifier in ordered:
                return
            if len(ordered) >= max_nodes:
                overflow["nodes"] = True
                return
            ordered[identifier] = _node(row, hop=1, placement=placement)
            rows_by_id[identifier] = row

        if parent_id and parent_id in live:
            keep(live[parent_id], "parent")
        for identifier in dict.fromkeys(
            str(row["target_id"] if row["source_id"] == center.id else row["source_id"])
            for row in touching
        ):
            if identifier in live:
                keep(live[identifier], "related")
        for row in reader.children(center.id, max_nodes + 1):
            keep(row, "child")
        for identifier in dict.fromkeys(
            str(row["target_id"] if row["source_id"] == center.id else row["source_id"])
            for row in proposed
        ):
            if identifier in live:
                keep(live[identifier], "related")

        nodes = list(ordered.values())
        drawn_ids = [node.id for node in nodes]
        names = {node.id: node.name for node in nodes}

        # The lines, kept in the order the nodes were: the folder's line to the center, what
        # was published about the center, what the center contains, what was proposed about
        # it, and then the lines among the other drawn nodes. So a cap never drops the line
        # that brought a node in while keeping the node.
        edges: dict[str, tuple[int, GraphEdge]] = {}
        if reader.containment:
            for node in nodes:
                parent = rows_by_id[node.id]["parent_entry_id"]
                if (
                    parent
                    and str(parent) in ordered
                    and rows_by_id[node.id]["entry_kind"] == "filesystem"
                ):
                    edge = _containment_edge(ordered[str(parent)], node)
                    rank = 0 if node.id == center.id else 2 if str(parent) == center.id else 5
                    edges[edge.id] = (rank, edge)
        reader.hold(drawn_ids)
        ladders, ladders_total, standing = _ladders(
            catalog,
            context,
            run_id,
            nodes,
            center=center.id,
            max_ladders=max_ladders,
            lines_of_work=lines_of_work,
        )
        nodes = [node.model_copy(update={"ladder": standing.get(node.id)}) for node in nodes]
        for rank, rows, proposal in (
            (1, touching, False),
            (3, proposed, True),
            (4, reader.among(center.id, max_edges + 1), False),
            (6, reader.proposed_among(center.id, max_edges + 1), True),
        ):
            for row in rows:
                if str(row["source_id"]) in ordered and str(row["target_id"]) in ordered:
                    edge = _relation_edge(row, names, proposed=proposal)
                    edges.setdefault(edge.id, (rank, edge))
        ranked = sorted(
            edges.values(),
            key=lambda held: (
                held[0],
                held[1].primitive,
                held[1].source,
                held[1].target,
                held[1].id,
            ),
        )
        overflow["edges"] = (
            len(ranked) > max_edges or len(touching) > max_edges or len(proposed) > max_edges
        )
        drawn_edges = [edge for _, edge in ranked[:max_edges]]

        totals = reader.totals(centre_row)
        unresolved_count, unresolved_rows = reader.unresolved()
        not_drawn = reader.not_drawn(center.id, relations)
        mentions = (
            _mentions(catalog, context.project, centre_row)
            if Primitive.REFERENCES.value in relations
            else None
        )

    if totals is None:
        nodes_left: int | None = None
        edges_left: int | None = None
        # Caps are reached only where the queries saw more than they kept.
        capped = overflow["nodes"] or overflow["edges"]
    else:
        nodes_left = max(0, totals[0] - len(nodes))
        edges_left = max(0, totals[1] - len(drawn_edges))
        capped = bool(nodes_left or edges_left)
    drawn: dict[str, int] = {}
    for edge in drawn_edges:
        drawn[edge.kind] = drawn.get(edge.kind, 0) + 1
    alone = len(nodes) == 1
    return NeighborhoodResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        center_id=center.id,
        hops=hops,
        relations=list(relations),
        include_candidates=candidates,
        nodes=nodes,
        edges=drawn_edges,
        truncation=Truncation(
            capped=capped,
            max_nodes=max_nodes,
            max_edges=max_edges,
            nodes_left_out=nodes_left,
            edges_left_out=edges_left,
            count_unknown=totals is None,
            message=_left_out_message(nodes_left, edges_left, capped=capped),
        ),
        availability=Availability(
            state="no_matches" if alone else "drawn",
            message=(
                "Nothing else is joined to it by these relations in this run, which is not "
                "evidence that nothing is."
                if alone
                else "A line not drawn is not evidence of absence."
            ),
            drawn=drawn,
            unresolved=UnresolvedSummary(
                count=unresolved_count,
                items=[_unresolved_item(row) for row in unresolved_rows],
            ),
            mentions=mentions,
            not_drawn=not_drawn,
        ),
        pinned=_pinned(context, run_id, is_pinned=is_pinned),
        ladders=ladders,
        ladders_total=ladders_total,
        ladders_left_out=max(0, ladders_total - len(ladders)),
    )


# ---------------------------------------------------------------------------
# One line's evidence.
# ---------------------------------------------------------------------------


def _end(catalog: Catalog, identifier: str) -> EdgeEnd:
    found = _rows(
        catalog.query(
            "SELECT name, relative_path, is_deleted FROM entries WHERE entry_id = ? LIMIT 1",
            [identifier],
        )
    )
    if not found or found[0]["is_deleted"]:
        return EdgeEnd(id=identifier, name=None, path=None, is_entry=False)
    return EdgeEnd(
        id=identifier,
        name=str(found[0]["name"]),
        path=str(found[0]["relative_path"] or ""),
        is_entry=True,
    )


def _statement(catalog: Catalog, rule_id: str | None, version: str | None) -> str | None:
    if rule_id is None:
        return None
    from ingence.core.schema import Rule
    from ingence.rulebook.render import render_rule

    rows = _rows(
        catalog.query(
            "SELECT * FROM rules WHERE rule_id = ? AND version = ? LIMIT 1", [rule_id, version]
        )
    )
    if not rows:
        return None
    try:
        return render_rule(record_from_row(rows[0], Rule))
    except Exception:  # a rule an older engine wrote: its statement as stored
        return str(rows[0].get("statement") or "") or None


def _loaded(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    held = json.loads(value) if isinstance(value, str) else value
    return [dict(item) for item in held or ()]


def _evidence(row: Mapping[str, Any]) -> tuple[list[SupportItem], list[DefeaterItem], list[str]]:
    support = [
        SupportItem(
            name=str(item.get("name", "")),
            family=str(item.get("family", "")),
            value=float(item.get("value", 0.0)),
            observations=[str(o) for o in item.get("observations", ())],
        )
        for item in _loaded(row.get("support"))
    ]
    defeaters = [
        DefeaterItem(
            reason=str(item.get("reason", "")),
            rule_id=str(item.get("rule_id", "")),
            rule_version=str(item.get("rule_version", "")),
            fired=bool(item.get("fired", False)),
            observations=[str(o) for o in item.get("observations", ())],
        )
        for item in _loaded(row.get("defeaters"))
    ]
    cited = sorted(
        {o for item in support for o in item.observations}
        | {o for item in defeaters for o in item.observations}
    )
    return support, defeaters, cited


def _top_folder(path: str | None) -> str:
    """The top-level folder a pair's file lies in, as the Proposed pairs page filters by it."""
    from ingence.api.routes.pages import TOP_LEVEL

    parts = posixpath.normpath(path or "").split("/")
    return parts[0] if len(parts) > 1 else TOP_LEVEL


def edge_detail(context: V1Context, query: Mapping[str, Sequence[str]]) -> EdgeDetail:
    """Everything one line rests on: relation, direction, run, rule, tier, support, records."""
    identifier = (_one(query, "id") or "").strip()
    if not identifier:
        raise ApiError("id names the line to explain", parameter="id")
    run_id, _ = _run(context, query)
    if run_id is None:
        message = "this project has no published run, so there is no line to explain"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    with open_catalog(context.project, run_id=run_id) as catalog:
        if identifier.startswith(_CONTAINMENT):
            return _containment_detail(catalog, run_id, identifier)
        rows = _rows(
            catalog.query("SELECT * FROM assertions WHERE edge_id = ? LIMIT 1", [identifier])
        )
        if rows:
            return _assertion_detail(catalog, run_id, rows[0])
        rows = _rows(
            catalog.query("SELECT * FROM candidates WHERE candidate_id = ? LIMIT 1", [identifier])
        )
        if rows:
            # A pair the run promoted is explained as the published edge it became.
            promoted = _rows(
                catalog.query(
                    "SELECT * FROM assertions WHERE candidate_id = ? AND decision_status = ? "
                    "ORDER BY edge_id LIMIT 1",
                    [identifier, DecisionStatus.ASSERTED.value],
                )
            )
            if promoted:
                return _assertion_detail(catalog, run_id, promoted[0])
            return _candidate_detail(catalog, run_id, rows[0])
    message = f"run {run_id} holds no line {identifier}"
    raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")


def _containment_detail(catalog: Catalog, run_id: str, identifier: str) -> EdgeDetail:
    try:
        parent_id, child_id = identifier[len(_CONTAINMENT) :].split(":", 1)
    except ValueError:
        message = f"{identifier} is not a containment line"
        raise ApiError(
            message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id"
        ) from None
    child = _rows(
        catalog.query(
            "SELECT parent_entry_id, is_deleted, entry_kind FROM entries WHERE entry_id = ? "
            "LIMIT 1",
            [child_id],
        )
    )
    if (
        not child
        or child[0]["is_deleted"]
        or str(child[0]["parent_entry_id"]) != parent_id
        # A member promoted from inside a file sits in it by embeds, not in a directory.
        or child[0]["entry_kind"] != "filesystem"
    ):
        message = f"run {run_id} holds no containment line {identifier}"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")
    source, target = _end(catalog, parent_id), _end(catalog, child_id)
    recorded = _rows(
        catalog.query(
            "SELECT edge_id, rule_id, rule_version, inference_tier FROM assertions "
            "WHERE primitive = 'contains' AND source_id = ? AND target_id = ? "
            "ORDER BY edge_id LIMIT 1",
            [parent_id, child_id],
        )
    )
    rule_id = str(recorded[0]["rule_id"]) if recorded else None
    version = str(recorded[0]["rule_version"]) if recorded else None
    properties = PRIMITIVE_PROPERTIES[Primitive.CONTAINS]
    return EdgeDetail(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        id=identifier,
        kind="indexed_containment",
        primitive=Primitive.CONTAINS.value,
        specialization="directory_of",
        respect=None,
        directed=properties.directed,
        symmetric=properties.symmetric,
        reading=(
            f"From the indexed directory: {target.name} is inside {source.name}, as the census "
            "found it when it listed the folder."
        ),
        definition=properties.definition,
        source=source,
        target=target,
        rule_id=rule_id,
        rule_version=version,
        rule_statement=_statement(catalog, rule_id, version),
        tier=InferenceTier.ENTAILED.value,
        tier_definition=INFERENCE_TIER_PROPERTIES[InferenceTier.ENTAILED].definition,
        status="observed",
        reference_resolution=None,
        level="file",
        quotient_origin=None,
        expansion_state=None,
        candidate_id=None,
        support=[],
        defeaters=[],
        records=[str(recorded[0]["edge_id"])] if recorded else [],
        review=None,
    )


def _assertion_detail(catalog: Catalog, run_id: str, row: Mapping[str, Any]) -> EdgeDetail:
    primitive = str(row["primitive"])
    properties = PRIMITIVE_PROPERTIES[Primitive(primitive)]
    source, target = _end(catalog, str(row["source_id"])), _end(catalog, str(row["target_id"]))
    support, defeaters, cited = _evidence(row)
    tier = str(row["inference_tier"])
    status = str(row["decision_status"])
    reading = _reading(
        primitive, source.name or source.id, target.name or target.id, row.get("respect")
    )
    if status != DecisionStatus.ASSERTED.value:
        reading = f"Not a current fact ({status}): {reading}"
    else:
        reading = f"Published by this run: {reading}"
        if row.get("candidate_id"):
            reading += f" (promoted from the proposed pair {row['candidate_id']})"
    if not target.is_entry:
        reading += f". What it names, {target.id}, is not an item of this run"
    return EdgeDetail(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        id=str(row["edge_id"]),
        kind="published_assertion",
        primitive=primitive,
        specialization=row.get("specialization"),
        respect=row.get("respect"),
        directed=properties.directed,
        symmetric=properties.symmetric,
        reading=reading + ".",
        definition=properties.definition,
        source=source,
        target=target,
        rule_id=str(row["rule_id"]),
        rule_version=str(row["rule_version"]),
        rule_statement=_statement(catalog, str(row["rule_id"]), str(row["rule_version"])),
        tier=tier,
        tier_definition=INFERENCE_TIER_PROPERTIES[InferenceTier(tier)].definition,
        status=status,
        reference_resolution=row.get("reference_resolution"),
        level=row.get("level"),
        quotient_origin=row.get("quotient_origin"),
        expansion_state=row.get("expansion_state"),
        candidate_id=str(row["candidate_id"]) if row.get("candidate_id") else None,
        support=support,
        defeaters=defeaters,
        records=[
            str(row["edge_id"]),
            *([str(row["candidate_id"])] if row.get("candidate_id") else []),
            *cited,
        ],
        review=None,
    )


def _candidate_detail(catalog: Catalog, run_id: str, row: Mapping[str, Any]) -> EdgeDetail:
    primitive = str(row["primitive"])
    properties = PRIMITIVE_PROPERTIES[Primitive(primitive)]
    source, target = _end(catalog, str(row["source_id"])), _end(catalog, str(row["target_id"]))
    support, defeaters, cited = _evidence(row)
    tier = str(row["decision_tier"])
    reading = _reading(
        primitive, source.name or source.id, target.name or target.id, row.get("respect")
    )
    top = _top_folder(source.path)
    return EdgeDetail(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        id=str(row["candidate_id"]),
        kind="proposed_candidate",
        primitive=primitive,
        specialization=row.get("specialization"),
        respect=row.get("respect"),
        directed=properties.directed,
        symmetric=properties.symmetric,
        reading=f"A rule proposed this and nobody has asserted it: {reading}.",
        definition=properties.definition,
        source=source,
        target=target,
        rule_id=str(row["rule_id"]),
        rule_version=str(row["rule_version"]),
        rule_statement=_statement(catalog, str(row["rule_id"]), str(row["rule_version"])),
        tier=tier,
        tier_definition=DECISION_TIER_PROPERTIES[DecisionTier(tier)].definition,
        status="proposed",
        reference_resolution=None,
        level=row.get("level"),
        quotient_origin=None,
        expansion_state=None,
        candidate_id=str(row["candidate_id"]),
        support=support,
        defeaters=defeaters,
        records=[str(row["candidate_id"]), *cited],
        review=ReviewRoute(
            view="candidates",
            rule=str(row["rule_id"]),
            top=top,
            message=(
                "Decided on the Suggested pairs page, where every answer is saved in the "
                "answer log; the graph writes nothing."
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Finding a center by its path.
# ---------------------------------------------------------------------------

#: How many matches one request may ask for.
FIND_LIMITS = (1, 100)


class FoundEntry(ApiModel):
    id: Identifier
    name: str
    path: str
    entry_type: str


@register("graph_find")
class FindResponse(ApiModel):
    """``GET /api/v1/graph/find?q=``: live folders and files whose path holds the words typed.

    Beside the search, which answers with experiments and folders, so any single file can be a
    center: matched without case on its path under its root, or exactly by its identifier,
    shallowest first. The characters typed are matched as themselves, never as a pattern.
    """

    schema_version: str
    run_id: Identifier
    query: str
    matches: list[FoundEntry]
    #: Whether more matched than the limit let through.
    more: bool


def _like(text: str) -> str:
    """A LIKE pattern matching ``text`` anywhere, with its own wildcards escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def find(context: V1Context, query: Mapping[str, Sequence[str]]) -> FindResponse:
    """Live entries whose path holds the words typed, or whose identifier is them."""
    typed = (_one(query, "q") or "").strip()
    if not typed:
        raise ApiError("q is the part of a path, or the identifier, to look for", parameter="q")
    limit = _integer(query, "limit", 20, *FIND_LIMITS)
    run_id, _ = _run(context, query)
    if run_id is None:
        message = "this project has no published run, so there is nothing to find"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    with open_catalog(context.project, run_id=run_id) as catalog:
        # A part recovered from inside a file shares its container's path, so a path finds the
        # container alone and a part is found by its identifier, under its document.
        rows = _rows(
            catalog.query(
                "SELECT entry_id, name, relative_path, entry_type FROM entries "  # noqa: S608
                f"WHERE NOT is_deleted AND (entry_id = ? OR ({filesystem_entry()} "
                "AND lower(relative_path) LIKE lower(?) ESCAPE '\\')) "
                "ORDER BY entry_id <> ?, depth, relative_path, entry_id LIMIT ?",
                [typed, _like(typed), typed, limit + 1],
            )
        )
    return FindResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        query=typed,
        matches=[
            FoundEntry(
                id=str(row["entry_id"]),
                name=str(row["name"] or row["relative_path"] or row["entry_id"]),
                path=str(row["relative_path"] or ""),
                entry_type=str(row["entry_type"]),
            )
            for row in rows[:limit]
        ],
        more=len(rows) > limit,
    )


# ---------------------------------------------------------------------------
# The routes.
# ---------------------------------------------------------------------------


@route("graph/neighborhood", model=NeighborhoodResponse)
def neighborhood_route(
    context: V1Context, query: Mapping[str, Sequence[str]]
) -> NeighborhoodResponse:
    return neighborhood(context, query)


@route("graph/edge", model=EdgeDetail)
def edge_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> EdgeDetail:
    return edge_detail(context, query)


@route("graph/find", model=FindResponse)
def find_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> FindResponse:
    return find(context, query)
