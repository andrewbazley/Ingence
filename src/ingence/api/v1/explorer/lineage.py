"""``GET /api/v1/explorer/lineage``: how files led to an output, where lineages fork and join.

The route answers two questions. How did these files and folders lead to this output? Where do
lineages branch or join? From a file, or from a folder's files that carry provenance, it walks the
run's lines a bounded number of hops upstream, downstream or both, and answers a small directed
graph:

- Published derivations (``assertions``, ``derives``, asserted) are the lineage itself.
- Published mentions (``references``) are drawn and walked, and never become "made from". They keep
  their primitive to the page, which draws them apart. For layout only, a mention flows from the
  file mentioned to the file that mentions it (a script after what it names).
- Proposals (``candidates`` nobody promoted) are walked only when asked for, labeled proposed, each
  with the Record's latest decision on it where a person made one.
- Folders are not edges here. Every file names its folder, so the page draws folders as clusters
  around their files, and a folder a rule reads as an experiment says so.
- Groups (the run's ``groups.*`` claims: replicates of one condition, one name's identity) are
  memberships. They are drawn to a group's own mark and asked for separately. A group never asserts
  that one file was made from another.

The route is bounded and exact about what it left out. Each hop is one query keyed on its
frontier. The parameters ``max_nodes`` and ``max_edges`` cap the answer. A node with more than
``fan`` neighbors one way has them gathered by folder and by kind of line into an aggregate node
that ``open`` expands. One aggregate holds one kind of line, so a mention is never drawn as a
derivation. Every node carries the exact number of its lines one way or the other that are not
drawn, so the page offers to expand one branch at a time (``expand``). A line whose other end is
not an entry of the run is an open end. The route says so and never draws it to an invented node.

Name families: a file drawn names its name family (``LineageNode.family``), the identifier
``explorer/lineage/families`` serves it under, so a view can collapse the files drawn by family.
That sibling route serves the whole run's lineage, or one folder's, with each family as one node
and the lines between families as one edge each (:mod:`.families`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

import duckdb
import pyarrow as pa

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.explorer.families import families_of
from ingence.api.v1.explorer.projection import connect as explorer_connection
from ingence.api.v1.explorer.wheel import _decisions, _run_and_projection, _unit_status
from ingence.api.v1.graph import PinnedRun, _pinned
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import boolean, integer, one
from ingence.core.schema import DecisionStatus
from ingence.rulebook.predicates.answered import ANSWERED_COPIES_PREDICATE
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.api.v1.explorer.projection import ExplorerProjection
    from ingence.storage.catalog import Catalog

__all__ = ["LineageResponse", "lineage"]

DEFAULT_DEPTH = 3
DEPTH_LIMITS = (1, 12)
DEFAULT_MAX_NODES = 160
NODE_LIMITS = (4, 800)
DEFAULT_MAX_EDGES = 400
EDGE_LIMITS = (4, 2000)
#: Neighbors one way beyond which a node's are gathered by folder into aggregates.
DEFAULT_FAN = 12
FAN_LIMITS = (2, 200)
#: The files a folder's lineage starts from at most: those carrying provenance, by name.
FOLDER_SEEDS = 60
#: Members an opened aggregate shows at most.
OPEN_LIMIT = 200
#: Group claims, and how many members a group lists by name.
GROUP_PREDICATES = "groups.%"
GROUP_MEMBERS_LISTED = 12
#: Groups drawn at most: those holding most of the drawn files first.
GROUPS_DRAWN = 24

Direction = Literal["up", "down", "both"]
OpenEndReason = Literal["not_an_entry", "entry_deleted", "folder", "inside_a_container"]
EdgeKind = Literal[
    "published_derivation",
    "published_mention",
    "proposed_derivation",
    "proposed_mention",
    "group_member",
    # A relation a person stated in the Record (``api/routes/overlay``): walked like a line so
    # its other end is drawn, never read as provenance the run asserts.
    "person_relation",
]


# ---------------------------------------------------------------------------
# The response models.
# ---------------------------------------------------------------------------


class LinkEnd(ApiModel):
    """Where an archive link points, as recorded; resolved only where the target is an entry."""

    target_as_recorded: str | None
    resolved_to: Identifier | None
    #: ``outside_root``, ``resolves``, ``broken`` or ``unreadable``: the census's words.
    state: str


class LineageNode(ApiModel):
    #: An entry, or ``agg:<node>:<up|down>:<folder>:<kind>:<primitive>`` for files gathered
    #: together: one folder's files joined to the node by one kind of line.
    id: Identifier
    kind: Literal["file", "folder", "aggregate"]
    name: str
    path: str
    folder_id: Identifier | None
    folder_name: str | None
    folder_path: str | None
    extension: str
    #: The file's kind family, as the Wheel's ``kind`` measure; ``folder`` for a folder.
    file_kind: str
    exam: str
    stage: str
    prov: str
    is_focus: bool
    #: Hops from the focus: negative upstream, positive downstream, 0 for the focus.
    hop: int
    #: Lines one way not drawn: the page offers to expand them, one branch at a time.
    hidden_up: Count
    hidden_down: Count
    #: An aggregate: how many files it stands for, their kinds, a few of their extensions.
    members: Count
    member_kinds: dict[str, Count]
    member_extensions: list[str]
    link: LinkEnd | None
    #: The name family the file belongs to, as ``explorer/lineage/families`` names it, or None
    #: where it is in none, so that a view can collapse the files drawn by family.
    family: Identifier | None = None


class Decision(ApiModel):
    kind: str
    actor: str
    at: str


class LineageEdge(ApiModel):
    #: An assertion's ``edge_id``, a candidate's ``candidate_id``, ``agg:...`` for the lines to
    #: an aggregate, or ``member:<claim_id>`` for a group membership.
    id: Identifier
    source: Identifier
    target: Identifier
    kind: EdgeKind
    #: One of the eight primitives, exactly as recorded.
    primitive: str
    #: The layout's direction: from what came first to what came of it. For a derivation the
    #: record's own; for a mention, from the file mentioned to the file mentioning it.
    flow_from: Identifier
    flow_to: Identifier
    rule_id: str | None
    rule_version: str | None
    tier: str | None
    reference_resolution: str | None
    #: The candidate a published line was promoted from, or a proposal's own.
    candidate_id: Identifier | None
    #: The Record's latest decision on a proposal, where a person made one.
    decision: Decision | None
    #: For a line to an aggregate: how many lines it stands for.
    lines: Count


class LineageGroup(ApiModel):
    #: ``group:<digest>`` of the predicate and the group's key.
    id: Identifier
    predicate: str
    rule_id: str
    #: The group's key as the rule wrote it, shortened for a label.
    label: str
    members_total: Count
    #: The members drawn here.
    members_shown: list[Identifier]


class OpenEnd(ApiModel):
    """A line whose other end is not an entry of the run: said, never drawn to a made-up node."""

    edge_id: Identifier
    at: Identifier
    primitive: str
    kind: EdgeKind
    #: What the record names at the other end, as recorded.
    named: str
    reference_resolution: str | None
    #: Why nothing is drawn there: no entry of the run carries it (``not_an_entry``), a file
    #: deleted before this run (``entry_deleted``), a folder of the run (``folder``: a lineage
    #: draws files, and a folder as the cluster around them), or a member inside a container
    #: file (``inside_a_container``: not a file of the folder hierarchy).
    reason: OpenEndReason


class FolderMark(ApiModel):
    """A folder drawn as a cluster: its name and whether a rule reads it as an experiment."""

    id: Identifier
    name: str
    path: str
    unit_status: str


class LineageTruncation(ApiModel):
    capped: bool
    max_nodes: Count
    max_edges: Count
    fan: Count
    #: Lines one way or the other from a drawn node that are not drawn, exactly.
    lines_not_drawn: Count
    message: str


class LineageProvenance(ApiModel):
    #: The run's lines, whole.
    published_derivations: Count
    published_mentions: Count
    proposed_pairs: Count
    #: Lines drawn here, by kind.
    drawn: dict[str, Count]
    message: str


@register("explorer_lineage")
class LineageResponse(ApiModel):
    """``GET /api/v1/explorer/lineage``: a bounded lineage around one file or folder."""

    schema_version: str
    run_id: Identifier
    pinned: PinnedRun
    focus: Identifier
    focus_kind: Literal["file", "folder"]
    direction: Direction
    depth: Count
    include_proposals: bool
    include_groups: bool
    #: The files a folder's lineage started from (for a file, the file).
    seeds: list[Identifier]
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    groups: list[LineageGroup]
    folders: list[FolderMark]
    open_ends: list[OpenEnd]
    truncation: LineageTruncation
    provenance: LineageProvenance


# ---------------------------------------------------------------------------
# The walk.
# ---------------------------------------------------------------------------


@dataclass
class _Line:
    edge_id: str
    source: str
    target: str
    primitive: str
    proposed: bool
    rule_id: str | None
    rule_version: str | None
    tier: str | None
    resolution: str | None
    candidate_id: str | None
    #: A person's stated relation, whose ``primitive`` is the relation they named.
    person: bool = False

    @property
    def flow_from(self) -> str:
        # A copy comes after its original, and a mention after what it mentions; a relation of
        # the same experiment has no direction and keeps the order it was said in.
        forward = self.primitive in ("derives", "same_experiment")
        return self.source if forward else self.target

    @property
    def flow_to(self) -> str:
        forward = self.primitive in ("derives", "same_experiment")
        return self.target if forward else self.source

    @property
    def kind(self) -> EdgeKind:
        if self.person:
            return "person_relation"
        mention = self.primitive == "references"
        if self.proposed:
            return "proposed_mention" if mention else "proposed_derivation"
        return "published_mention" if mention else "published_derivation"


@dataclass
class _Walk:
    catalog: Catalog
    proposals: bool
    #: A person's stated relations, walked beside the run's lines where a page asks for them.
    people: list[_Line] = field(default_factory=list)
    lines: dict[str, _Line] = field(default_factory=dict)
    #: The live files met so far (``live``) and their folders (``folders``), the ends found deleted
    #: (``deleted``), the ends that are live entries but no file, such as a folder or a member
    #: inside a container (``contained``), and every end looked up (``checked``).
    live: dict[str, str] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    folders: set[str] = field(default_factory=set)
    contained: set[str] = field(default_factory=set)
    checked: set[str] = field(default_factory=set)

    def _hold(self, name: str, ids: Sequence[str]) -> None:
        self.catalog.connection.register(name, pa.table({"id": pa.array(list(ids), pa.string())}))

    def step(self, frontier: Sequence[str], way: Literal["up", "down"]) -> list[_Line]:
        """Every line one hop ``way`` from the frontier, published first, then proposed."""
        if not frontier:
            return []
        self._hold("lineage_frontier", frontier)
        # Upstream of a node: the sources of derivations into it and the files it mentions.
        if way == "up":
            derives, mentions = "target_id", "source_id"
        else:
            derives, mentions = "source_id", "target_id"
        found: list[_Line] = []
        rows = self.catalog.query(
            "SELECT edge_id, source_id, target_id, primitive, rule_id, rule_version, "  # noqa: S608
            "inference_tier, reference_resolution, candidate_id FROM assertions "
            "WHERE decision_status = ? AND ("
            f"(primitive = 'derives' AND {derives} IN (SELECT id FROM lineage_frontier)) OR "
            f"(primitive = 'references' AND {mentions} IN (SELECT id FROM lineage_frontier))) "
            "ORDER BY primitive, edge_id",
            [DecisionStatus.ASSERTED.value],
        ).fetchall()
        for edge, source, target, primitive, rule, version, tier, resolution, candidate in rows:
            found.append(
                _Line(
                    str(edge),
                    str(source),
                    str(target),
                    str(primitive),
                    False,
                    str(rule),
                    str(version),
                    str(tier),
                    resolution,
                    candidate,
                )
            )
        if self.proposals:
            rows = self.catalog.query(
                "SELECT candidate_id, source_id, target_id, primitive, rule_id, rule_version, "  # noqa: S608
                "decision_tier FROM candidates c WHERE ("
                f"(c.primitive = 'derives' AND c.{derives} IN (SELECT id FROM lineage_frontier)) "
                f"OR (c.primitive = 'references' AND c.{mentions} IN "
                "(SELECT id FROM lineage_frontier))) AND NOT EXISTS (SELECT 1 FROM assertions a "
                "WHERE a.candidate_id = c.candidate_id AND a.decision_status = ?) "
                "ORDER BY c.primitive, c.candidate_id",
                [DecisionStatus.ASSERTED.value],
            ).fetchall()
            for candidate, source, target, primitive, rule, version, tier in rows:
                found.append(
                    _Line(
                        str(candidate),
                        str(source),
                        str(target),
                        str(primitive),
                        True,
                        str(rule),
                        str(version),
                        str(tier),
                        None,
                        str(candidate),
                    )
                )
        held = set(frontier)
        for line in self.people:
            if (line.flow_to if way == "up" else line.flow_from) in held:
                found.append(line)
        for line in found:
            self.lines.setdefault(line.edge_id, line)
        return found

    def liveness(self, ids: Sequence[str]) -> None:
        """Learn which of these ends are live files, and their folders; the rest are open ends,
        each with what it is: deleted, a folder, a member inside a container, or no entry."""
        wanted = [identifier for identifier in dict.fromkeys(ids) if identifier not in self.checked]
        if not wanted:
            return
        self._hold("lineage_ends", wanted)
        for entry, folder, deleted, directory, filesystem in self.catalog.query(
            "SELECT entry_id, parent_entry_id, is_deleted, entry_type = 'directory', "
            "entry_kind = 'filesystem' FROM entries WHERE entry_id IN (SELECT id FROM lineage_ends)"
        ).fetchall():
            if deleted:
                if filesystem:
                    self.deleted.add(str(entry))
            elif not filesystem:
                self.contained.add(str(entry))
            elif directory:
                self.folders.add(str(entry))
            else:
                self.live[str(entry)] = str(folder or "")
        self.checked.update(wanted)

    def reason(self, end: str) -> OpenEndReason:
        """Why an end that is no live file is not drawn, in the open end's words."""
        if end in self.deleted:
            return "entry_deleted"
        if end in self.folders:
            return "folder"
        if end in self.contained:
            return "inside_a_container"
        return "not_an_entry"

    def not_drawn(
        self,
        drawn: Sequence[str],
        gathered: set[tuple[str, str]] | None = None,
    ) -> dict[str, tuple[int, int]]:
        """For each drawn node, its lines up and down that lead to a file not drawn.

        An expansion could draw these lines. The count never includes a line drawn inside an
        aggregate (``gathered`` holds each aggregate's anchor and member). It never includes a line
        whose other end is not a live file of the run: that is an open end, which the route says
        and no expansion can draw.
        """
        if not drawn:
            return {}
        self._hold("lineage_drawn", drawn)
        published = (
            "SELECT source_id, target_id, primitive FROM assertions WHERE decision_status = ? "
            "AND primitive IN ('derives', 'references') AND (source_id IN "
            "(SELECT id FROM lineage_drawn) OR target_id IN (SELECT id FROM lineage_drawn))"
        )
        parameters: list[Any] = [DecisionStatus.ASSERTED.value]
        union = published
        if self.proposals:
            union += (
                " UNION ALL SELECT source_id, target_id, primitive FROM candidates c WHERE "
                "c.primitive IN ('derives', 'references') AND (c.source_id IN "
                "(SELECT id FROM lineage_drawn) OR c.target_id IN (SELECT id FROM lineage_drawn)) "
                "AND NOT EXISTS (SELECT 1 FROM assertions a WHERE a.candidate_id = c.candidate_id "
                "AND a.decision_status = ?)"
            )
            parameters.append(DecisionStatus.ASSERTED.value)
        rows = self.catalog.query(
            f"""
            WITH lines AS ({union}),
            flows AS (
                SELECT CASE WHEN primitive = 'derives' THEN source_id ELSE target_id END AS up,
                       CASE WHEN primitive = 'derives' THEN target_id ELSE source_id END AS down
                FROM lines
            ),
            files AS (
                SELECT entry_id FROM entries WHERE NOT is_deleted
                AND entry_kind = 'filesystem' AND entry_type <> 'directory'
            )
            SELECT down AS node, up AS other, true AS upward FROM flows
            WHERE down IN (SELECT id FROM lineage_drawn)
              AND up NOT IN (SELECT id FROM lineage_drawn)
              AND up IN (SELECT entry_id FROM files)
            UNION ALL
            SELECT up, down, false FROM flows
            WHERE up IN (SELECT id FROM lineage_drawn)
              AND down NOT IN (SELECT id FROM lineage_drawn)
              AND down IN (SELECT entry_id FROM files)
            """,  # noqa: S608
            parameters,
        ).fetchall()
        counted: dict[str, list[int]] = {}
        for node, other, upward in rows:
            if gathered and (str(node), str(other)) in gathered:
                continue
            held = counted.setdefault(str(node), [0, 0])
            held[0 if upward else 1] += 1
        return {node: (up, down) for node, (up, down) in counted.items()}


@dataclass(frozen=True)
class _Gathered:
    """Files one hop ``way`` from ``anchor``, in one folder, joined to it by one kind of line."""

    anchor: str
    way: str
    folder: str
    members: list[str]
    kind: EdgeKind
    primitive: str


#: What an aggregate's lines are, in its name: never "made from" for a mention or a proposal.
_GATHERED_WORDS: dict[str, str] = {
    "published_derivation": "published derivations",
    "published_mention": "published mentions",
    "proposed_derivation": "proposed derivations",
    "proposed_mention": "proposed mentions",
}


def _gathered_words(kind: str, primitive: str) -> str:
    if kind == "person_relation":
        return f"a person's relation: {primitive.replace('_', ' ')}"
    return _GATHERED_WORDS.get(kind, kind.replace("_", " "))


def _entries(
    connection: duckdb.DuckDBPyConnection, projection: ExplorerProjection, ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    connection.register("lineage_ids", pa.table({"id": pa.array(list(ids), pa.string())}))
    columns = [
        "entry_id",
        "name",
        "relative_path",
        "folder_id",
        "extension",
        "kind",
        "exam",
        "stage",
        "prov",
        "symlink_state",
        "symlink_target",
        "is_archive_link",
    ]
    rows = connection.execute(
        f"SELECT {', '.join(columns)} FROM {projection.files} "  # noqa: S608
        "WHERE entry_id IN (SELECT id FROM lineage_ids)"
    ).fetchall()
    return {str(row[0]): dict(zip(columns, row, strict=True)) for row in rows}


def _folders(
    connection: duckdb.DuckDBPyConnection, projection: ExplorerProjection, ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    connection.register("lineage_folder_ids", pa.table({"id": pa.array(list(ids), pa.string())}))
    rows = connection.execute(
        "SELECT entry_id, name, relative_path, exp_claim_id, exp_claim_ids, exp_conflicting "  # noqa: S608
        f"FROM {projection.folders} WHERE entry_id IN (SELECT id FROM lineage_folder_ids)"
    ).fetchall()
    columns = [
        "entry_id",
        "name",
        "relative_path",
        "exp_claim_id",
        "exp_claim_ids",
        "exp_conflicting",
    ]
    return {str(row[0]): dict(zip(columns, row, strict=True)) for row in rows}


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _split(text: str | None) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def lineage(context: V1Context, query: Mapping[str, Sequence[str]]) -> LineageResponse:
    """A bounded lineage around one file or folder (see the module)."""
    focus_id = (one(query, "focus") or "").strip()
    if not focus_id:
        raise ApiError("focus names the file or folder the lineage starts from", parameter="focus")
    direction_text = (one(query, "direction") or "both").strip()
    if direction_text not in ("up", "down", "both"):
        message = f"direction is up, down or both; {direction_text!r} is none of them"
        raise ApiError(message, parameter="direction")
    direction: Direction = direction_text  # type: ignore[assignment]
    depth = integer(
        query, "depth", default=DEFAULT_DEPTH, low=DEPTH_LIMITS[0], high=DEPTH_LIMITS[1]
    )
    max_nodes = integer(
        query, "max_nodes", default=DEFAULT_MAX_NODES, low=NODE_LIMITS[0], high=NODE_LIMITS[1]
    )
    max_edges = integer(
        query, "max_edges", default=DEFAULT_MAX_EDGES, low=EDGE_LIMITS[0], high=EDGE_LIMITS[1]
    )
    fan = integer(query, "fan", default=DEFAULT_FAN, low=FAN_LIMITS[0], high=FAN_LIMITS[1])
    proposals = boolean(query, "proposals")
    groups_wanted = boolean(query, "groups")
    expand = set(_split(one(query, "expand")))
    opened = set(_split(one(query, "open")))
    run_id, is_pinned, projection, _ = _run_and_projection(context, query)

    connection = explorer_connection()
    try:
        focus_file = _entries(connection, projection, [focus_id])
        focus_folder = {} if focus_file else _folders(connection, projection, [focus_id])
        if not focus_file and not focus_folder:
            message = f"run {run_id} holds no live file or folder {focus_id}"
            raise ApiError(
                message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="focus"
            )
        focus_kind: Literal["file", "folder"] = "file" if focus_file else "folder"
        if focus_kind == "file":
            seeds = [focus_id]
        else:
            seeds = [
                str(row[0])
                for row in connection.execute(
                    f"SELECT entry_id FROM {projection.files} "  # noqa: S608
                    "WHERE list_contains(ancestors, ?) AND prov <> 'none' "
                    "ORDER BY relative_path, entry_id LIMIT ?",
                    [focus_id, FOLDER_SEEDS],
                ).fetchall()
            ]
    finally:
        connection.close()

    hops: dict[str, int] = dict.fromkeys(seeds, 0)
    aggregates: dict[str, _Gathered] = {}
    used: dict[str, _Line] = {}
    capped = False

    # A person's stated relations, walked too unless the page asks not to (``people=0``). A "not
    # related" statement says that no line exists, and it is never walked as one.
    people: list[_Line] = []
    if one(query, "people") != "0":
        from ingence.api.routes.overlay import standing_layer

        people = [
            _Line(target, source, other, relation, False, None, None, "person", None, None, True)
            for target, source, other, relation in standing_layer(context.project).relations
            if relation != "not_related"
        ]
    with open_catalog(context.project, run_id=run_id) as catalog:
        walk = _Walk(catalog, proposals, people)
        walk.liveness(seeds)
        live = walk.live

        def advance(
            frontier: list[str], way: Literal["up", "down"], sign: int, level: int
        ) -> list[str]:
            nonlocal capped
            found = walk.step(frontier, way)
            walk.liveness([line.source for line in found] + [line.target for line in found])
            by_node: dict[str, list[_Line]] = {}
            for line in found:
                here = line.flow_to if way == "up" else line.flow_from
                if here in frontier:
                    by_node.setdefault(here, []).append(line)
            following: list[str] = []
            for here in frontier:
                lines = by_node.get(here, [])
                others = [line.flow_from if way == "up" else line.flow_to for line in lines]
                known = [other for other in others if other in live]
                if len(set(known) - set(hops)) > fan and here not in expand:
                    # Too many one way: gathered by folder and by kind of line, so one aggregate
                    # holds one kind of line and its edge says which. A file joined by two kinds is
                    # a member of each, one line in each.
                    by_group: dict[tuple[str, EdgeKind, str], list[str]] = {}
                    for line in lines:
                        other = line.flow_from if way == "up" else line.flow_to
                        if other in hops or other not in live:
                            continue
                        group = by_group.setdefault((live[other], line.kind, line.primitive), [])
                        if other not in group:
                            group.append(other)
                    keyed = {
                        f"agg:{here}:{way}:{folder}:{kind}:{primitive}": (folder, kind, primitive)
                        for folder, kind, primitive in by_group
                    }
                    # Every opened aggregate's files are drawn first, so a file joined by two
                    # kinds is never drawn by one and still counted by the other.
                    for key, group_key in sorted(keyed.items()):
                        if key not in opened:
                            continue
                        for member in by_group[group_key][:OPEN_LIMIT]:
                            if member in hops:
                                continue
                            if len(hops) >= max_nodes:
                                capped = True
                                break
                            hops[member] = sign * level
                            following.append(member)
                    for key, (folder, kind, primitive) in sorted(keyed.items()):
                        if key in opened:
                            continue
                        members = [m for m in by_group[(folder, kind, primitive)] if m not in hops]
                        if members:
                            aggregates[key] = _Gathered(here, way, folder, members, kind, primitive)
                    for line in lines:
                        other = line.flow_from if way == "up" else line.flow_to
                        if other in hops or other not in live:
                            used.setdefault(line.edge_id, line)
                    continue
                for line in lines:
                    other = line.flow_from if way == "up" else line.flow_to
                    if other not in live:
                        used.setdefault(line.edge_id, line)
                        continue
                    if other not in hops:
                        if len(hops) + len(aggregates) >= max_nodes:
                            capped = True
                            continue
                        hops[other] = sign * level
                        following.append(other)
                    used.setdefault(line.edge_id, line)
            return following

        ways: list[tuple[Literal["up", "down"], int]] = []
        if direction in ("up", "both"):
            ways.append(("up", -1))
        if direction in ("down", "both"):
            ways.append(("down", 1))
        for way, sign in ways:
            frontier = list(seeds)
            for level in range(1, depth + 1):
                if not frontier:
                    break
                frontier = advance(frontier, way, sign, level)
            # One branch at a time: what the page asked to expand goes one hop further.
            extra = [node for node in sorted(expand) if node in hops]
            if extra:
                advance(extra, way, sign, depth + 1)

        drawn = list(hops)
        in_aggregates = {
            (held.anchor, member) for held in aggregates.values() for member in held.members
        }
        hidden = walk.not_drawn(drawn, in_aggregates)
        all_lines = list(walk.lines.values())
        # Lines among drawn nodes not met on the walk's own path (a join seen from both sides).
        for line in all_lines:
            if line.flow_from in hops and line.flow_to in hops:
                used.setdefault(line.edge_id, line)

        groups: list[LineageGroup] = []
        group_edges: list[LineageEdge] = []
        if groups_wanted and drawn:
            groups, group_edges = _groups(catalog, [node for node in drawn if node in live])
        family_of = families_of(catalog, [node for node in drawn if node in live])

    decisions = _proposal_decisions(context, [line for line in used.values() if line.proposed])

    connection = explorer_connection()
    try:
        rows = _entries(connection, projection, drawn)
        aggregate_members = sorted(
            {member for held in aggregates.values() for member in held.members}
        )
        member_rows = _entries(connection, projection, aggregate_members[: OPEN_LIMIT * 4])
        folder_ids = sorted(
            {str(row["folder_id"]) for row in rows.values() if row["folder_id"]}
            | {held.folder for held in aggregates.values()}
            | ({focus_id} if focus_kind == "folder" else set())
        )
        folder_rows = _folders(connection, projection, folder_ids)
    finally:
        connection.close()
    unit_decisions = _decisions(context.project)

    nodes: list[LineageNode] = []
    for node in drawn:
        row = rows.get(node)
        if row is None:
            continue
        up, down = hidden.get(node, (0, 0))
        folder = folder_rows.get(str(row["folder_id"]))
        nodes.append(
            LineageNode(
                id=node,
                kind="file",
                name=str(row["name"]),
                path=str(row["relative_path"]),
                folder_id=str(row["folder_id"]) if row["folder_id"] else None,
                folder_name=str(folder["name"]) if folder else None,
                folder_path=str(folder["relative_path"]) if folder else None,
                extension=str(row["extension"] or ""),
                file_kind=str(row["kind"]),
                exam=str(row["exam"]),
                stage=str(row["stage"]),
                prov=str(row["prov"]),
                is_focus=node == focus_id,
                hop=hops[node],
                hidden_up=up,
                hidden_down=down,
                members=1,
                member_kinds={},
                member_extensions=[],
                link=_link(row, rows),
                family=family_of.get(node),
            )
        )
    if focus_kind == "folder":
        folder = folder_rows.get(focus_id, {})
        nodes.insert(
            0,
            LineageNode(
                id=focus_id,
                kind="folder",
                name=str(folder.get("name") or focus_id),
                path=str(folder.get("relative_path") or ""),
                folder_id=None,
                folder_name=None,
                folder_path=None,
                extension="",
                file_kind="folder",
                exam="",
                stage="",
                prov="",
                is_focus=True,
                hop=0,
                hidden_up=0,
                hidden_down=0,
                members=len(seeds),
                member_kinds={},
                member_extensions=[],
                link=None,
            ),
        )
    edges: list[LineageEdge] = []
    for key, gathered in sorted(aggregates.items()):
        anchor, side, folder_id, members = (
            gathered.anchor,
            gathered.way,
            gathered.folder,
            gathered.members,
        )
        kinds: dict[str, int] = {}
        extensions: list[str] = []
        for member in members:
            held = member_rows.get(member)
            if held is None:
                continue
            kinds[str(held["kind"])] = kinds.get(str(held["kind"]), 0) + 1
            extension = str(held["extension"] or "")
            if extension and extension not in extensions and len(extensions) < 4:
                extensions.append(extension)
        folder = folder_rows.get(folder_id)
        nodes.append(
            LineageNode(
                id=key,
                kind="aggregate",
                name=(
                    f"{len(members):,} file{'' if len(members) == 1 else 's'} in "
                    f"{folder['name'] if folder else 'a folder'} "
                    f"({_gathered_words(gathered.kind, gathered.primitive)})"
                ),
                path=str(folder["relative_path"]) if folder else "",
                folder_id=folder_id,
                folder_name=str(folder["name"]) if folder else None,
                folder_path=str(folder["relative_path"]) if folder else None,
                extension="",
                file_kind=max(kinds, key=lambda name: kinds[name]) if kinds else "unknown",
                exam="",
                stage="",
                prov="",
                is_focus=False,
                hop=(hops.get(anchor, 0) + (-1 if side == "up" else 1)),
                hidden_up=0,
                hidden_down=0,
                members=len(members),
                member_kinds=dict(sorted(kinds.items())),
                member_extensions=extensions,
                link=None,
            )
        )
        held_members = set(members)
        lines_to = [
            line
            for line in walk.lines.values()
            if (line.flow_to if side == "up" else line.flow_from) == anchor
            and (line.flow_from if side == "up" else line.flow_to) in held_members
            and line.kind == gathered.kind
            and line.primitive == gathered.primitive
        ]
        # The rule, where every line gathered was concluded by one; else none is named.
        rules = {(line.rule_id, line.rule_version) for line in lines_to}
        rule_id, rule_version = next(iter(rules)) if len(rules) == 1 else (None, None)
        source, target = (key, anchor) if side == "up" else (anchor, key)
        edges.append(
            LineageEdge(
                id=key,
                source=source,
                target=target,
                kind=gathered.kind,
                primitive=gathered.primitive,
                flow_from=source,
                flow_to=target,
                rule_id=rule_id,
                rule_version=rule_version,
                tier=None,
                reference_resolution=None,
                candidate_id=None,
                decision=None,
                lines=len(lines_to),
            )
        )

    open_ends: list[OpenEnd] = []
    drawn_set = set(hops)
    for line in sorted(used.values(), key=lambda held: held.edge_id):
        if line.source in drawn_set and line.target in drawn_set:
            decided = decisions.get(line.edge_id) if line.proposed else None
            edges.append(
                LineageEdge(
                    id=line.edge_id,
                    source=line.source,
                    target=line.target,
                    kind=line.kind,
                    primitive=line.primitive,
                    flow_from=line.flow_from,
                    flow_to=line.flow_to,
                    rule_id=line.rule_id,
                    rule_version=line.rule_version,
                    tier=line.tier,
                    reference_resolution=line.resolution,
                    candidate_id=line.candidate_id,
                    decision=Decision(kind=decided[0], actor=decided[1], at=decided[2])
                    if decided
                    else None,
                    lines=1,
                )
            )
        elif (line.source in drawn_set) != (line.target in drawn_set):
            here, other = (
                (line.source, line.target)
                if line.source in drawn_set
                else (line.target, line.source)
            )
            if other not in live:
                open_ends.append(
                    OpenEnd(
                        edge_id=line.edge_id,
                        at=here,
                        primitive=line.primitive,
                        kind=line.kind,
                        named=other,
                        reference_resolution=line.resolution,
                        reason=walk.reason(other),
                    )
                )
    if len(edges) > max_edges:
        capped = True
        edges = edges[:max_edges]
    edges.extend(group_edges)

    marks = [
        FolderMark(
            id=folder_id,
            name=str(row["name"]),
            path=str(row["relative_path"] or ""),
            unit_status=_unit_status({**row, "proposed_groups": 0}, unit_decisions)[0],
        )
        for folder_id, row in sorted(folder_rows.items())
    ]
    not_drawn = sum(up + down for up, down in hidden.values())
    drawn_kinds: dict[str, int] = {}
    for edge in edges:
        drawn_kinds[edge.kind] = drawn_kinds.get(edge.kind, 0) + edge.lines
    meta = projection.meta.get("provenance", {})
    derivations = int(meta.get("published_derivations", 0))
    mentions = int(meta.get("published_mentions", 0))
    proposed = int(meta.get("proposed_pairs", 0))
    focus_lines = sum(1 for edge in edges if edge.kind != "group_member")
    if not derivations and not mentions:
        said = "This run published no provenance anywhere: no derivation and no mention. " + (
            f"{proposed:,} pair{'' if proposed == 1 else 's'} are proposed; turn on "
            "Proposals to see those near here."
            if proposed and not proposals
            else "What is drawn is the file, its folder and the groups it belongs to."
        )
    elif not focus_lines:
        said = (
            "Nothing this run published joins "
            + ("this file" if focus_kind == "file" else "any file beneath this folder")
            + " to another, which is not evidence that nothing does."
        )
    else:
        said = f"Drawn from this run's published lines{' and proposals' if proposals else ''}."
    return LineageResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        pinned=_pinned(context, run_id, is_pinned=is_pinned),
        focus=focus_id,
        focus_kind=focus_kind,
        direction=direction,
        depth=depth,
        include_proposals=proposals,
        include_groups=groups_wanted,
        seeds=seeds,
        nodes=nodes,
        edges=edges,
        groups=groups,
        folders=marks,
        open_ends=open_ends,
        truncation=LineageTruncation(
            capped=capped or bool(not_drawn),
            max_nodes=max_nodes,
            max_edges=max_edges,
            fan=fan,
            lines_not_drawn=not_drawn,
            message=(
                f"{not_drawn:,} line{'' if not_drawn == 1 else 's'} from drawn files lead further "
                "than drawn: expand a file's branch to follow them."
                if not_drawn
                else "Every line from every drawn file is drawn."
            )
            + (" The caps were reached." if capped else ""),
        ),
        provenance=LineageProvenance(
            published_derivations=derivations,
            published_mentions=mentions,
            proposed_pairs=proposed,
            drawn=drawn_kinds,
            message=said,
        ),
    )


def _link(row: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> LinkEnd | None:
    state = str(row.get("symlink_state") or "not_a_link")
    if state == "not_a_link":
        return None
    return LinkEnd(
        target_as_recorded=row.get("symlink_target"),
        resolved_to=None,
        state=state,
    )


def _proposal_decisions(
    context: V1Context, lines: Sequence[_Line]
) -> dict[str, tuple[str, str, str]]:
    if not lines:
        return {}
    from ingence.record.answers import answers_read, carried_confirmations, stored_evidence
    from ingence.record.log import latest_by_target, read_events

    events = read_events(context.project)
    latest = latest_by_target(events)
    found: dict[str, tuple[str, str, str]] = {}
    for line in lines:
        event = latest.get(f"candidate:{line.edge_id}")
        if event is not None:
            found[line.edge_id] = (event.kind.value, event.actor, event.occurred_at)
    # A pair confirmed under another name is drawn with that answer: the same two files, the same
    # way round, on the evidence answered (``record/answers.py``).
    answers = answers_read(events)
    rest = [
        line
        for line in lines
        if line.edge_id not in found
        and line.rule_id
        and frozenset((line.source, line.target)) in answers.pairs
    ]
    if rest:
        with open_catalog(context.project) as catalog:
            evidence = stored_evidence(catalog, [line.edge_id for line in rest])
        carried = carried_confirmations(
            [(line.edge_id, line.source, line.target, str(line.rule_id)) for line in rest],
            answers,
            evidence,
        )
        for line in rest:
            named = carried.get(line.edge_id)
            event = latest.get(named[0]) if named else None
            if event is not None:
                found[line.edge_id] = (event.kind.value, event.actor, event.occurred_at)
    return found


def _groups(catalog: Catalog, nodes: Sequence[str]) -> tuple[list[LineageGroup], list[LineageEdge]]:
    """The groups the drawn files belong to, and one membership line each.

    A file that a person said is unrelated to the rest of a set is not drawn in it. A claim
    carrying the fired defeater of a rule reading a person's copies answers is left out, so the
    Graph does not call the set copies. Every other claim is drawn.
    """
    catalog.connection.register(
        "lineage_group_nodes", pa.table({"id": pa.array(list(nodes), pa.string())})
    )
    rows = catalog.query(
        "SELECT claim_id, subject, predicate, object_reference, rule_id, rule_version, "
        "defeaters_json "
        "FROM claims WHERE predicate LIKE ? AND subject IN (SELECT id FROM lineage_group_nodes) "
        "ORDER BY predicate, object_reference, subject",
        [GROUP_PREDICATES],
    ).fetchall()
    readers = {
        str(rule_id)
        for (rule_id,) in catalog.query(
            "SELECT rule_id FROM rules WHERE predicate = ?", [ANSWERED_COPIES_PREDICATE]
        ).fetchall()
    }
    keyed: dict[str, dict[str, Any]] = {}
    edges: list[LineageEdge] = []
    for claim_id, subject, predicate, key, rule_id, version, defeaters in rows:
        if readers and _refused_by_a_person(defeaters, readers):
            continue
        identifier = f"group:{_digest(str(predicate), str(key))}"
        held = keyed.setdefault(
            identifier,
            {"predicate": str(predicate), "key": str(key), "rule": str(rule_id), "members": []},
        )
        held["members"].append(str(subject))
        edges.append(
            LineageEdge(
                id=f"member:{claim_id}",
                source=str(subject),
                target=identifier,
                kind="group_member",
                primitive="equivalent",
                flow_from=str(subject),
                flow_to=identifier,
                rule_id=str(rule_id),
                rule_version=str(version),
                tier=None,
                reference_resolution=None,
                candidate_id=None,
                decision=None,
                lines=1,
            )
        )
    groups: list[LineageGroup] = []
    kept: set[str] = set()
    for identifier, held in sorted(keyed.items()):
        total = catalog.query(
            "SELECT count(*) FROM claims WHERE predicate = ? AND object_reference = ?",
            [held["predicate"], held["key"]],
        ).fetchone()
        members_total = int(total[0]) if total else len(held["members"])
        # A group of one groups nothing; the page draws at most GROUPS_DRAWN, largest first.
        if members_total < 2:
            continue
        kept.add(identifier)
        groups.append(
            LineageGroup(
                id=identifier,
                predicate=held["predicate"],
                rule_id=held["rule"],
                label=_group_label(held["predicate"], held["key"]),
                members_total=members_total,
                members_shown=held["members"][:GROUP_MEMBERS_LISTED],
            )
        )
    groups.sort(key=lambda group: (-len(group.members_shown), -group.members_total, group.id))
    groups = groups[:GROUPS_DRAWN]
    shown = {group.id for group in groups}
    return groups, [edge for edge in edges if edge.target in shown and edge.target in kept]


#: A group's kind in plain words, by its predicate's last part; anything else says its own name.
_GROUP_WORDS = {
    "identical_by_content_digest": "identical bytes",
    "copies_by_sampled_digest": "copies (sampled bytes)",
    "replicates_of_one_condition": "replicates",
    "same_name_identity": "same name",
    "name_family_without_stages": "name family",
    "same_observed_metadata_outside_programs": "same metadata",
    "archive_link_copy_of_a_file": "archive copy",
}


def _refused_by_a_person(defeaters: object, readers: set[str]) -> bool:
    """Whether a claim's defeaters hold a person's "Unrelated", fired."""
    try:
        held = json.loads(str(defeaters or "[]"))
    except ValueError:
        return False
    return any(
        isinstance(item, dict) and item.get("fired") and item.get("rule_id") in readers
        for item in held
        if isinstance(held, list)
    )


def _group_label(predicate: str, key: str) -> str:
    """A short label for a group: its kind in plain words and what its members share."""
    last = predicate.split(".", 1)[-1]
    kind = _GROUP_WORDS.get(last, last.replace("_", " "))
    try:
        said = json.loads(key)
    except ValueError:
        return kind
    if isinstance(said, dict) and isinstance(said.get("components"), list):
        words = [
            str(part[1])
            for part in said["components"]
            if isinstance(part, list) and len(part) > 1 and part[0] != "extension"
        ]
        return f"{kind}: {' '.join(words)}"[:60] if words else kind
    return kind


@route("explorer/lineage", model=LineageResponse)
def lineage_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> LineageResponse:
    return lineage(context, query)
