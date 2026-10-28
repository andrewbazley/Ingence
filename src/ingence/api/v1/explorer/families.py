"""``GET /api/v1/explorer/lineage/families``: Lineage at scale, name families collapsed.

A name family is a set of two or more files whose names agree on every word once stage words (raw,
processed, analysis) are left out. The engine already forms them. The grouping rule whose respect
is ``name_family`` (``name_family_without_stages`` in the shipped Rulebook) concludes one claim per
file, and the claims' key is the family. This route reads those claims. It forms no family of its
own and invents no similarity, so a person who turns the rule off, or edits its words, changes what
is collapsed here and nowhere else. The parameter ``rules`` names other grouping rules to read
families from instead (the rule that groups a name's every word but a replicate's, say). An
identifier that is not a grouping rule of the run is refused.

The route answers the question that a many-file Lineage cannot: which groups of similarly named
files lead to which others, as one small graph.

- Members. A file is a member of its family where its claim of the grouping rule is resolved,
  outside every folder raised as an installed program (the resolved claims of
  ``review/claim_classes.belongs``). A claim in conflict, defeated by a rule or by a person's
  "Wrong" on the family, makes no member. A family a person refused is not collapsed, and a family
  held together only by claims in conflict is not a family. (``belongs`` keeps a claim that only a
  person's answer defeated, as it stood before the answer, so the class can be counted and
  answered again. A view that collapses files has to read the answer as given.) Each family node
  says how many files are in conflict with it (``members_in_conflict``).
- Nodes. A family is one node with its count of files. A file that has a line (a published
  derivation or mention, or a proposal where asked) and belongs to no family is a node of its own
  (``kind: file``). A file with no line is in no node here. Lineage never draws a file that
  nothing joins, and the family's count says how many of its files there are in all.
- The quotient graph. An edge joins two nodes when any file of one has a line to a file of the
  other, with ``lines``, the number of file-to-file lines it stands for. One edge holds one kind of
  line (``published_derivation``, ``published_mention``, ``proposed_derivation`` or
  ``proposed_mention``), so a mention is never drawn as made-from. Lines between two files of one
  family are not an edge. They are counted on the family (``lines_inside``).
- Expandable. ``family=<id>`` opens one family: its files, and every line that touches one of
  them, each saying which node the other end belongs to.

The route is bounded and exact about what it left out. The parameter ``limit`` caps the nodes (the
ones with most lines first, ties broken by label then identifier), ``edge_limit`` caps the edges,
``members_limit`` caps the files of an opened family, and ``members_offset`` is the file its page
starts at (a page at a time, the rest counted). Every cap says how many it left out. The
truncation counts the whole graph: its edges, those among the nodes drawn, those between a drawn
node and one not drawn, and those among nodes not drawn, with their lines. Nothing is cut without a
count. Everything is read from the run's own tables, by queries over temporary tables of the
request's own connection. The order is fixed: the same run and the same request give the same
answer.

A line whose other end is not a live file (a folder, a file deleted before the run, a member inside
a container, no entry at all) is an open end. It is counted in ``provenance.lines_to_open_ends``
and never drawn to an invented node.
"""

from __future__ import annotations

import hashlib
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import pyarrow as pa

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.graph import PinnedRun, _pinned
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import boolean, integer, one, run_named
from ingence.core.schema import DecisionStatus
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.storage.catalog import Catalog

__all__ = ["LineageFamiliesResponse", "families", "families_of"]

DEFAULT_LIMIT = 100
LIMITS = (1, 1000)
DEFAULT_EDGE_LIMIT = 300
EDGE_LIMITS = (1, 5000)
DEFAULT_MEMBERS_LIMIT = 100
MEMBERS_LIMITS = (1, 1000)
#: The furthest file an opened family may start its page at.
MAX_MEMBERS_OFFSET = 10_000_000
#: File names a node shows as examples, and extensions it lists.
EXAMPLES_SHOWN = 3
EXTENSIONS_SHOWN = 4
#: The respect a grouping rule states when it groups names into families.
FAMILY_RESPECT = "name_family"
#: The two relations the lineage follows: derivation, and mention.
RELATIONS = ("derives", "references")
#: Separates the parts of a node's key; a control character no name or key holds.
_SEP = "\x1f"

LineKind = Literal[
    "published_derivation", "published_mention", "proposed_derivation", "proposed_mention"
]
FamiliesState = Literal["found", "no_rule", "none_formed"]


# ---------------------------------------------------------------------------
# The response models.
# ---------------------------------------------------------------------------


class FamilyNode(ApiModel):
    #: ``family:<digest>`` for a family, the entry identifier for a file.
    id: Identifier
    kind: Literal["family", "file"]
    #: A family: the words its names share. A file: its name.
    label: str
    #: A family: its files in the scope asked for, whether or not they have a line. A file: 1.
    #: Only files whose claim of the grouping rule is resolved count, outside every installed
    #: program; a file whose claim is in conflict is no member (see ``members_in_conflict``).
    members: Count
    #: Its files that have at least one line here (drawn, or counted at an open end).
    members_with_lines: Count
    #: Files the grouping rule put in this family but whose claim is in conflict (a rule defeated
    #: it, or a person said the family is wrong): left out of ``members`` and never collapsed with
    #: them, counted here so the state is not lost. 0 for a file.
    members_in_conflict: Count
    #: Lines that touch it, each counted once, lines between two of its own files included.
    lines: Count
    #: Lines between two files of this family: not an edge, counted here.
    lines_inside: Count
    #: Up to three names of its files that have a line, in path order.
    examples: list[str]
    #: Up to four extensions of those files, in order of first appearance in path order.
    extensions: list[str]
    #: How many folders its files lie in.
    folders: Count
    #: The grouping rule that formed the family; None for a file.
    rule_id: str | None
    #: A file's path under its root; None for a family.
    path: str | None


class FamilyEdge(ApiModel):
    #: ``quotient:<digest>`` of the two nodes and the kind of line.
    id: Identifier
    #: The record's own direction: for a derivation, the file it was made from is the source; for
    #: a mention, the file that mentions is the source.
    source: Identifier
    target: Identifier
    kind: LineKind
    #: ``derives`` or ``references``, as recorded.
    primitive: str
    #: The layout's direction: from what came first to what came of it (a mention flows from the
    #: file mentioned to the file that mentions it).
    flow_from: Identifier
    flow_to: Identifier
    #: How many file-to-file lines this edge stands for.
    lines: Count


class FamilyMember(ApiModel):
    id: Identifier
    name: str
    path: str
    extension: str
    folder_path: str | None
    #: Lines that touch this file.
    lines: Count
    #: Of those, the lines that lead to it (it was made from the other file, or mentioned by it)
    #: and the lines that lead from it (the other file was made from it, or is mentioned by it),
    #: in the layout's direction (``FamilyEdge.flow_from`` to ``flow_to``): ``lines`` is their sum.
    lines_in: Count
    lines_out: Count


class FamilyLine(ApiModel):
    #: An assertion's ``edge_id``, or a candidate's ``candidate_id``.
    id: Identifier
    source: Identifier
    target: Identifier
    kind: LineKind
    primitive: str
    #: The node each end belongs to: a family's, or the file's own identifier.
    source_node: Identifier
    target_node: Identifier


class OpenFamily(ApiModel):
    """One family opened: its files and the lines that touch them."""

    id: Identifier
    label: str
    members_total: Count
    #: Files in conflict with the family, as on its node: not among ``members``.
    members_in_conflict: Count
    members: list[FamilyMember]
    #: The files after the ones listed (``members_offset`` and ``members``): counted, not listed.
    members_left_out: Count
    #: How many files come before the first one listed (a page at a time: ``members_offset``).
    members_offset: Count
    lines: list[FamilyLine]
    lines_total: Count
    lines_left_out: Count


class FamiliesRule(ApiModel):
    """What forms the families, and whether it formed any in this scope."""

    state: FamiliesState
    #: The grouping rules whose respect is a name family, in force in this run.
    rule_ids: list[str]
    #: Families of two or more files in the scope, and the files they hold.
    families: Count
    files_in_families: Count
    message: str


class FamiliesTruncation(ApiModel):
    capped: bool
    limit: Count
    edge_limit: Count
    nodes_total: Count
    nodes_left_out: Count
    #: Of ``nodes_total``, how many are name families and how many are files that have a line and
    #: belong to no family (a file a person's "Wrong" took out of its family is one of these). A
    #: node of the second kind is never a family: counts that say "families" say these first.
    families_total: Count
    files_total: Count
    #: Edges among the nodes drawn, and how many of them a cap left out.
    edges_total: Count
    edges_left_out: Count
    #: The whole graph: every edge between two nodes, whether or not either is drawn, and how
    #: they divide: among the nodes drawn (``edges_total``), between a drawn node and one not drawn,
    #: and among nodes not drawn. All exact.
    edges_in_graph: Count
    edges_to_nodes_not_drawn: Count
    edges_among_nodes_not_drawn: Count
    #: Lines between a drawn node and a node not drawn, and lines (of those edges) between two nodes
    #: not drawn; lines inside one family are ``lines_inside`` on it. Exact.
    lines_to_nodes_not_drawn: Count
    lines_among_nodes_not_drawn: Count
    message: str


class FamiliesProvenance(ApiModel):
    #: Lines of the chosen relations between two live files in the scope.
    lines: Count
    #: Of those, by kind.
    by_kind: dict[str, Count]
    #: Lines between two files of one family.
    lines_inside_families: Count
    #: Lines with one end in the scope and one outside it (only when a folder was named).
    lines_leaving_scope: Count
    #: Lines with an end that is no live file: said, never drawn to an invented node.
    lines_to_open_ends: Count
    message: str


@register("explorer_lineage_families")
class LineageFamiliesResponse(ApiModel):
    """``GET /api/v1/explorer/lineage/families``: the lineage with name families collapsed."""

    schema_version: str
    run_id: Identifier
    pinned: PinnedRun
    #: The folder the scope was limited to, or None for the whole run.
    folder: Identifier | None
    folder_name: str | None
    relations: list[str]
    include_proposals: bool
    nodes: list[FamilyNode]
    edges: list[FamilyEdge]
    open: OpenFamily | None
    families: FamiliesRule
    truncation: FamiliesTruncation
    provenance: FamiliesProvenance


# ---------------------------------------------------------------------------
# Reading a request.
# ---------------------------------------------------------------------------


def _relations(query: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    text = one(query, "relations")
    if text is None or not text.strip():
        return RELATIONS
    asked = [part.strip() for part in text.split(",") if part.strip()]
    unknown = [part for part in asked if part not in RELATIONS]
    if unknown:
        message = (
            f"the lineage follows {' and '.join(RELATIONS)}; "
            f"{', '.join(unknown)} is not one of them"
        )
        raise ApiError(message, parameter="relations")
    return tuple(name for name in RELATIONS if name in asked)


def _digest(*parts: str) -> str:
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()[:16]


def _family_id(rule_and_key: str) -> str:
    return f"family:{_digest(rule_and_key)}"


def families_of(catalog: Catalog, entry_ids: Sequence[str]) -> dict[str, str]:
    """Each of these live files' name family, where it has one: ``{entry id: family id}``.

    A family here is the same as in :func:`families`: the claim of a grouping rule whose respect is
    a name family, of two or more live files in the run. The identifier is the one the families
    route serves, so a Lineage drawn file by file can name each file's family and a page can join
    the two. A file in no family is left out of the answer.
    """
    rules = _family_rules(catalog)
    if not rules or not entry_ids:
        return {}
    _hold(catalog, "lineage_family_asked", "id", list(dict.fromkeys(entry_ids)))
    outside, regions = _outside_programs(catalog)
    asked = catalog.query(
        "SELECT c.subject, min(c.rule_id || chr(31) || c.object_reference) AS fkey FROM claims c "  # noqa: S608
        "JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.predicate LIKE 'groups.%' AND c.rule_id IN (SELECT unnest(?::VARCHAR[])) "
        "AND c.resolution_status = 'resolved' AND c.object_reference IS NOT NULL "
        f"AND c.subject IN (SELECT id FROM lineage_family_asked) {outside} GROUP BY c.subject",
        [list(rules), regions],
    ).fetchall()
    held = {str(subject): str(key) for subject, key in asked}
    if not held:
        return {}
    _hold(catalog, "lineage_family_keys", "fkey", sorted(set(held.values())))
    # Two or more live files in the whole run, members by the same reading.
    sizes = {
        str(key)
        for (key,) in catalog.query(
            "SELECT fkey FROM (SELECT c.subject, min(c.rule_id || chr(31) || c.object_reference) "  # noqa: S608
            "AS fkey FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.predicate LIKE 'groups.%' AND c.rule_id IN (SELECT unnest(?::VARCHAR[])) "
            "AND c.resolution_status = 'resolved' "
            "AND c.object_reference IS NOT NULL AND NOT e.is_deleted "
            "AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory' "
            "AND c.rule_id || chr(31) || c.object_reference IN "
            f"(SELECT fkey FROM lineage_family_keys) {outside} GROUP BY c.subject) "
            "GROUP BY fkey HAVING count(*) >= 2",
            [list(rules), regions],
        ).fetchall()
    }
    return {subject: _family_id(key) for subject, key in held.items() if key in sizes}


def _line_kind(primitive: str, *, proposed: bool) -> LineKind:
    mention = primitive == "references"
    if proposed:
        return "proposed_mention" if mention else "proposed_derivation"
    return "published_mention" if mention else "published_derivation"


def _words(key: str) -> str:
    """The words a family's names share, from the grouping rule's key (the components, with the
    extension left out), in the key's own order; the key itself where it is not read."""
    try:
        said = json.loads(key)
    except ValueError:
        return key[:60]
    components = said.get("components") if isinstance(said, dict) else said
    if not isinstance(components, list):
        return key[:60]
    words = [
        str(part[1])
        for part in components
        if isinstance(part, list) and len(part) > 1 and part[0] != "extension"
    ]
    return " ".join(words)[:80] if words else "names with no shared word"


def _hold(catalog: Catalog, name: str, column: str, values: Sequence[str]) -> None:
    catalog.connection.register(name, pa.table({column: pa.array(list(values), pa.string())}))


def _count(catalog: Catalog, sql: str, parameters: Sequence[Any] = ()) -> int:
    row = catalog.query(sql, list(parameters)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


# ---------------------------------------------------------------------------
# The temporary tables of one request.
# ---------------------------------------------------------------------------


def _outside_programs(catalog: Catalog) -> tuple[str, list[str]]:
    """The clause leaving out a claim about a file beneath a folder raised as an installed
    program's (the one definition's ``beneath_a_program``), and the regions it binds."""
    from ingence.api.routes.pages import _BENEATH_SQL
    from ingence.review.claim_classes import program_regions

    return f"AND NOT {_BENEATH_SQL}", program_regions(catalog)


def _family_rules(catalog: Catalog, asked: Sequence[str] | None = None) -> list[str]:
    """The grouping rules families are read from: those a request names (each must be a grouping
    rule of the run, else ``ApiError``), or the run's rules whose respect is a name family."""
    if asked is None:
        return [
            str(rule)
            for (rule,) in catalog.query(
                "SELECT DISTINCT rule_id FROM rules WHERE respect = ? AND effect = 'groups' "
                "ORDER BY rule_id",
                [FAMILY_RESPECT],
            ).fetchall()
        ]
    known = [
        str(rule)
        for (rule,) in catalog.query(
            "SELECT DISTINCT rule_id FROM rules WHERE effect = 'groups' ORDER BY rule_id"
        ).fetchall()
    ]
    unknown = [rule for rule in asked if rule not in known]
    if unknown:
        message = (
            f"{', '.join(unknown)} is no grouping rule of this run; "
            f"the grouping rules are {', '.join(known) or 'none'}"
        )
        raise ApiError(message, parameter="rules")
    return sorted(set(asked))


def _prepare(
    catalog: Catalog,
    *,
    folder: str | None,
    relations: Sequence[str],
    proposals: bool,
    rules: Sequence[str],
) -> None:
    """Build, on this request's own connection: the lines, the live files at their ends, each
    file's family where it has one, and every line with the node at each end."""
    placeholders = ", ".join("?" for _ in relations)
    sql = (
        "CREATE OR REPLACE TEMP TABLE lineage_lines AS "  # noqa: S608
        "SELECT edge_id AS line_id, source_id, target_id, primitive, 0 AS proposed "
        f"FROM assertions WHERE decision_status = ? AND primitive IN ({placeholders})"
    )
    parameters: list[Any] = [DecisionStatus.ASSERTED.value, *relations]
    if proposals:
        sql += (
            " UNION ALL SELECT candidate_id, source_id, target_id, primitive, 1 "  # noqa: S608
            f"FROM candidates c WHERE c.primitive IN ({placeholders}) AND NOT EXISTS "
            "(SELECT 1 FROM assertions a WHERE a.candidate_id = c.candidate_id "
            "AND a.decision_status = ?)"
        )
        parameters += [*relations, DecisionStatus.ASSERTED.value]
    catalog.query(sql, parameters)
    if folder is not None:
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_below AS WITH RECURSIVE below(entry_id) AS ("
            "SELECT CAST(? AS VARCHAR) UNION ALL SELECT e.entry_id FROM entries e "
            "JOIN below b ON e.parent_entry_id = b.entry_id WHERE NOT e.is_deleted "
            "AND e.entry_kind = 'filesystem') SELECT entry_id FROM below",
            [folder],
        )
        in_scope = "e.entry_id IN (SELECT entry_id FROM lineage_below)"
    else:
        in_scope = "true"
    # Every live file at the end of a line, and whether it is in the scope asked for.
    catalog.query(
        "CREATE OR REPLACE TEMP TABLE lineage_live AS "  # noqa: S608
        "SELECT e.entry_id, e.name, e.relative_path, e.parent_entry_id, "
        f"lower(coalesce(e.extension, '')) AS extension, ({in_scope}) AS in_scope "
        "FROM entries e WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' "
        "AND e.entry_type <> 'directory' AND e.entry_id IN "
        "(SELECT source_id FROM lineage_lines UNION SELECT target_id FROM lineage_lines)"
    )
    # Each file's family: one claim per file and rule; where two rules of the respect disagree the
    # first by rule and key wins, so one file is never in two nodes.
    scope_claims = "AND c.subject IN (SELECT entry_id FROM lineage_below)" if folder else ""
    if rules:
        outside, regions = _outside_programs(catalog)
        # A member is a file whose claim is resolved, outside every installed program
        # (``review/claim_classes.belongs``' resolved claims). A claim in conflict, defeated by a
        # rule or by a person's "Wrong" on the family, makes no member: such a family is not
        # collapsed, and the files in conflict are counted beside the family instead.
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_fam AS "  # noqa: S608
            "SELECT c.subject AS entry_id, "
            "min(c.rule_id || chr(31) || c.object_reference) AS fkey, "
            "min(e.parent_entry_id) AS parent_id FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.predicate LIKE 'groups.%' AND c.rule_id IN (SELECT unnest(?::VARCHAR[])) "
            "AND c.resolution_status = 'resolved' "
            "AND c.object_reference IS NOT NULL AND NOT e.is_deleted "
            "AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory' "
            f"{scope_claims} {outside} GROUP BY c.subject",
            [list(rules), regions],
        )
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_fam_conflict AS "  # noqa: S608
            "SELECT c.subject AS entry_id, "
            "min(c.rule_id || chr(31) || c.object_reference) AS fkey FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.predicate LIKE 'groups.%' AND c.rule_id IN (SELECT unnest(?::VARCHAR[])) "
            "AND c.resolution_status = 'examined_conflicting' "
            "AND c.object_reference IS NOT NULL AND NOT e.is_deleted "
            "AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory' "
            "AND c.subject NOT IN (SELECT entry_id FROM lineage_fam) "
            f"{scope_claims} {outside} GROUP BY c.subject",
            [list(rules), regions],
        )
        # A group of one groups nothing: only families of two or more files in the scope.
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_family_size AS "
            "SELECT fkey, count(*) AS members, count(DISTINCT parent_id) AS folders "
            "FROM lineage_fam GROUP BY fkey HAVING count(*) >= 2"
        )
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_family_files AS "
            "SELECT f.entry_id, f.fkey FROM lineage_fam f JOIN lineage_family_size s USING (fkey)"
        )
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_family_conflicts AS "
            "SELECT fkey, count(*) AS n FROM lineage_fam_conflict "
            "WHERE fkey IN (SELECT fkey FROM lineage_family_size) GROUP BY fkey"
        )
    else:
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_family_size("
            "fkey VARCHAR, members BIGINT, folders BIGINT)"
        )
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_family_files(entry_id VARCHAR, fkey VARCHAR)"
        )
        catalog.query(
            "CREATE OR REPLACE TEMP TABLE lineage_family_conflicts(fkey VARCHAR, n BIGINT)"
        )
    # The lines drawn: both ends live files in the scope, each end replaced by its node.
    catalog.query(
        "CREATE OR REPLACE TEMP TABLE lineage_ends AS "
        "SELECT l.line_id, l.source_id, l.target_id, l.primitive, l.proposed, "
        "coalesce('F' || chr(31) || fs.fkey, 'E' || chr(31) || l.source_id) AS a, "
        "coalesce('F' || chr(31) || ft.fkey, 'E' || chr(31) || l.target_id) AS b "
        "FROM lineage_lines l "
        "JOIN lineage_live s ON s.entry_id = l.source_id AND s.in_scope "
        "JOIN lineage_live t ON t.entry_id = l.target_id AND t.in_scope "
        "LEFT JOIN lineage_family_files fs ON fs.entry_id = l.source_id "
        "LEFT JOIN lineage_family_files ft ON ft.entry_id = l.target_id "
        "WHERE l.source_id <> l.target_id"
    )


def _node_id(key: str) -> str:
    """The identifier a node key is served under: a family's digest, a file's own identifier."""
    kind, _, rest = key.partition(_SEP)
    return _family_id(rest) if kind == "F" else rest


# ---------------------------------------------------------------------------
# The route.
# ---------------------------------------------------------------------------


def families(context: V1Context, query: Mapping[str, Sequence[str]]) -> LineageFamiliesResponse:
    """The lineage with name families collapsed, and its quotient graph (see the module)."""
    folder = (one(query, "folder") or "").strip() or None
    opened = (one(query, "family") or "").strip() or None
    relations = _relations(query)
    proposals = boolean(query, "proposals")
    rules_text = one(query, "rules")
    asked_rules = (
        [part.strip() for part in rules_text.split(",") if part.strip()]
        if rules_text and rules_text.strip()
        else None
    )
    limit = integer(query, "limit", default=DEFAULT_LIMIT, low=LIMITS[0], high=LIMITS[1])
    edge_limit = integer(
        query, "edge_limit", default=DEFAULT_EDGE_LIMIT, low=EDGE_LIMITS[0], high=EDGE_LIMITS[1]
    )
    members_limit = integer(
        query,
        "members_limit",
        default=DEFAULT_MEMBERS_LIMIT,
        low=MEMBERS_LIMITS[0],
        high=MEMBERS_LIMITS[1],
    )
    members_offset = integer(query, "members_offset", default=0, low=0, high=MAX_MEMBERS_OFFSET)
    run_id, is_pinned = run_named(context, query)
    if run_id is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)

    with open_catalog(context.project, run_id=run_id) as catalog:
        folder_name: str | None = None
        if folder is not None:
            row = catalog.query(
                "SELECT name, entry_type, is_deleted FROM entries WHERE entry_id = ? LIMIT 1",
                [folder],
            ).fetchone()
            if row is None or row[2] or row[1] != "directory":
                message = f"run {run_id} holds no live folder {folder}"
                raise ApiError(
                    message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="folder"
                )
            folder_name = str(row[0])
        rules = _family_rules(catalog, asked_rules)
        _prepare(catalog, folder=folder, relations=relations, proposals=proposals, rules=rules)
        nodes, nodes_total, top = _nodes(catalog, limit, rules)
        family_nodes = _count(
            catalog,
            "SELECT count(*) FROM lineage_node_lines WHERE node LIKE 'F' || chr(31) || '%'",
        )
        edges, graph = _edges(catalog, top, edge_limit)
        open_family = (
            _open(catalog, opened, edge_limit, members_limit, members_offset)
            if opened is not None
            else None
        )
        rule_state = _rule_state(catalog, rules, folder)
        provenance = _provenance(catalog, folder)

    nodes_left = max(0, nodes_total - len(nodes))
    edges_left = max(0, graph.among_drawn - len(edges))
    capped = bool(nodes_left or edges_left)
    return LineageFamiliesResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        pinned=_pinned(context, run_id, is_pinned=is_pinned),
        folder=folder,
        folder_name=folder_name,
        relations=list(relations),
        include_proposals=proposals,
        nodes=nodes,
        edges=edges,
        open=open_family,
        families=rule_state,
        truncation=FamiliesTruncation(
            capped=capped,
            limit=limit,
            edge_limit=edge_limit,
            nodes_total=nodes_total,
            nodes_left_out=nodes_left,
            families_total=family_nodes,
            files_total=nodes_total - family_nodes,
            edges_total=graph.among_drawn,
            edges_left_out=edges_left,
            edges_in_graph=graph.edges,
            edges_to_nodes_not_drawn=graph.to_not_drawn,
            edges_among_nodes_not_drawn=graph.among_not_drawn,
            lines_to_nodes_not_drawn=graph.lines_to_not_drawn,
            lines_among_nodes_not_drawn=graph.lines_among_not_drawn,
            message=_truncation_words(nodes_left, edges_left, graph),
        ),
        provenance=provenance,
    )


def _truncation_words(nodes_left: int, edges_left: int, graph: _Graph) -> str:
    if not nodes_left and not edges_left:
        return "Every node and every edge is drawn."
    said = []
    if nodes_left:
        said.append(f"{nodes_left:,} node{'' if nodes_left == 1 else 's'}")
    if edges_left:
        said.append(f"{edges_left:,} edge{'' if edges_left == 1 else 's'}")
    return (
        f"Left out by the limits: {' and '.join(said)}. The whole graph has {graph.edges:,} "
        f"edge{'' if graph.edges == 1 else 's'}: {graph.among_drawn:,} among the nodes drawn, "
        f"{graph.to_not_drawn:,} between a drawn node and one not drawn "
        f"({graph.lines_to_not_drawn:,} line{'' if graph.lines_to_not_drawn == 1 else 's'}), and "
        f"{graph.among_not_drawn:,} among nodes not drawn "
        f"({graph.lines_among_not_drawn:,} line{'' if graph.lines_among_not_drawn == 1 else 's'})."
    )


def _nodes(
    catalog: Catalog, limit: int, rules: Sequence[str]
) -> tuple[list[FamilyNode], int, list[str]]:
    """The nodes with most lines first (ties by label, then identifier), up to ``limit``; how many
    there are in all, and the keys of the ones returned."""
    catalog.query(
        "CREATE OR REPLACE TEMP TABLE lineage_node_lines AS "
        "SELECT node, count(*) AS lines FROM ("
        "SELECT a AS node, line_id FROM lineage_ends "
        "UNION ALL SELECT b, line_id FROM lineage_ends WHERE b <> a) GROUP BY node"
    )
    total = _count(catalog, "SELECT count(*) FROM lineage_node_lines")
    if not total:
        return [], 0, []
    # The nodes with most lines first, ties by label and then identifier: the cut-off is the line
    # count of the limit-th node, and every node at or above it is a candidate, so a tie is broken
    # by the words and never by the order the store holds them in.
    found = catalog.query(
        "SELECT lines FROM lineage_node_lines ORDER BY lines DESC LIMIT 1 OFFSET ?", [limit - 1]
    ).fetchone()
    cutoff = int(found[0]) if found else 0
    candidates = []
    for node, count, name in catalog.query(
        "SELECT t.node, t.lines, l.name FROM lineage_node_lines t "
        "LEFT JOIN lineage_live l ON t.node = 'E' || chr(31) || l.entry_id WHERE t.lines >= ?",
        [cutoff],
    ).fetchall():
        kind, _, rest = str(node).partition(_SEP)
        label = _words(rest.partition(_SEP)[2]) if kind == "F" else str(name or rest)
        candidates.append((-int(count), label, _node_id(str(node)), str(node)))
    candidates.sort()
    top = [node for _, _, _, node in candidates[:limit]]
    lines = {node: -negative for negative, _, _, node in candidates[:limit]}
    _hold(catalog, "lineage_top", "node", top)
    inside = {
        str(node): int(count)
        for node, count in catalog.query(
            "SELECT a, count(*) FROM lineage_ends WHERE a = b "
            "AND a IN (SELECT node FROM lineage_top) GROUP BY a"
        ).fetchall()
    }
    # Each node's files that have a line, with their names for the examples, in path order.
    members: dict[str, list[tuple[str, str, str]]] = {}
    for node, _entry, name, path, extension in catalog.query(
        "SELECT node, entry_id, name, relative_path, extension FROM ("
        "SELECT a AS node, source_id AS entry_id FROM lineage_ends "
        "UNION SELECT b, target_id FROM lineage_ends) n "
        "JOIN lineage_live USING (entry_id) WHERE node IN (SELECT node FROM lineage_top) "
        "ORDER BY node, relative_path, entry_id"
    ).fetchall():
        members.setdefault(str(node), []).append((str(name), str(path), str(extension)))
    sizes = {
        str(fkey): (int(count), int(folders))
        for fkey, count, folders in catalog.query(
            "SELECT fkey, members, folders FROM lineage_family_size WHERE "
            "'F' || chr(31) || fkey IN (SELECT node FROM lineage_top)"
        ).fetchall()
    }
    conflicts = {
        str(fkey): int(count)
        for fkey, count in catalog.query(
            "SELECT fkey, n FROM lineage_family_conflicts WHERE "
            "'F' || chr(31) || fkey IN (SELECT node FROM lineage_top)"
        ).fetchall()
    }
    drawn: list[FamilyNode] = []
    for key in top:
        held = members.get(key, [])
        names = [name for name, _, _ in held]
        extensions: list[str] = []
        for _, _, extension in held:
            if extension and extension not in extensions and len(extensions) < EXTENSIONS_SHOWN:
                extensions.append(extension)
        kind, _, rest = key.partition(_SEP)
        if kind == "F":
            total_members, folders = sizes.get(rest, (len(held), 0))
            rule_id, _, family_key = rest.partition(_SEP)
            drawn.append(
                FamilyNode(
                    id=_family_id(rest),
                    kind="family",
                    label=_words(family_key),
                    members=total_members,
                    members_with_lines=len(held),
                    members_in_conflict=conflicts.get(rest, 0),
                    lines=lines[key],
                    lines_inside=inside.get(key, 0),
                    examples=names[:EXAMPLES_SHOWN],
                    extensions=extensions,
                    folders=folders,
                    rule_id=rule_id,
                    path=None,
                )
            )
        else:
            name, path, _ = held[0] if held else (rest, "", "")
            drawn.append(
                FamilyNode(
                    id=rest,
                    kind="file",
                    label=name,
                    members=1,
                    members_with_lines=1,
                    members_in_conflict=0,
                    lines=lines[key],
                    lines_inside=0,
                    examples=[name],
                    extensions=extensions,
                    folders=1,
                    rule_id=None,
                    path=path,
                )
            )
    del rules
    return drawn, total, top


class _Graph(NamedTuple):
    """The whole graph's edges, by where their two nodes stand: all exact."""

    edges: int
    among_drawn: int
    to_not_drawn: int
    among_not_drawn: int
    lines_among_not_drawn: int
    lines_to_not_drawn: int


def _edges(
    catalog: Catalog, top: Sequence[str], edge_limit: int
) -> tuple[list[FamilyEdge], _Graph]:
    """The quotient graph's edges among the nodes drawn, most lines first, up to ``edge_limit``;
    and the whole graph's count of edges and lines by where their nodes stand."""
    if not top:
        return [], _Graph(0, 0, 0, 0, 0, 0)
    rows = catalog.query(
        "SELECT a, b, primitive, proposed, count(*) AS lines FROM lineage_ends "
        "WHERE a <> b AND a IN (SELECT node FROM lineage_top) "
        "AND b IN (SELECT node FROM lineage_top) GROUP BY a, b, primitive, proposed "
        "ORDER BY lines DESC, a, b, primitive, proposed LIMIT ?",
        [edge_limit],
    ).fetchall()
    edges: list[FamilyEdge] = []
    for first, second, primitive, proposed, count in rows:
        source, target = _node_id(str(first)), _node_id(str(second))
        kind = _line_kind(str(primitive), proposed=bool(proposed))
        # A mention flows from the file mentioned to the file that mentions it.
        forward = str(primitive) == "derives"
        edges.append(
            FamilyEdge(
                id=f"quotient:{_digest(source, target, kind)}",
                source=source,
                target=target,
                kind=kind,
                primitive=str(primitive),
                flow_from=source if forward else target,
                flow_to=target if forward else source,
                lines=int(count),
            )
        )
    # Every edge of the whole graph: one row per (from, to, kind), and the lines it stands for.
    whole = catalog.query(
        "SELECT count(*), count(*) FILTER (WHERE da AND db), count(*) FILTER (WHERE da <> db), "
        "count(*) FILTER (WHERE NOT da AND NOT db), "
        "coalesce(sum(n) FILTER (WHERE NOT da AND NOT db), 0), "
        "coalesce(sum(n) FILTER (WHERE da <> db), 0) FROM ("
        "SELECT a IN (SELECT node FROM lineage_top) AS da, b IN (SELECT node FROM lineage_top) "
        "AS db, n FROM (SELECT a, b, count(*) AS n FROM lineage_ends WHERE a <> b "
        "GROUP BY a, b, primitive, proposed))"
    ).fetchone()
    assert whole is not None
    return edges, _Graph(*(int(value or 0) for value in whole))


def _open(
    catalog: Catalog, family: str, edge_limit: int, members_limit: int, members_offset: int = 0
) -> OpenFamily:
    """One family's files, a page at a time, and every line touching one of them."""
    found = [
        str(key)
        for (key,) in catalog.query("SELECT fkey FROM lineage_family_size ORDER BY fkey").fetchall()
        if _family_id(str(key)) == family
    ]
    if not found:
        message = f"no family {family} of two or more files in this run and scope"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="family")
    fkey = found[0]
    total = _count(catalog, "SELECT members FROM lineage_family_size WHERE fkey = ?", [fkey])
    rows = catalog.query(
        "SELECT e.entry_id, e.name, e.relative_path, lower(coalesce(e.extension, '')), "
        "p.relative_path FROM lineage_family_files f JOIN entries e ON e.entry_id = f.entry_id "
        "LEFT JOIN entries p ON p.entry_id = e.parent_entry_id WHERE f.fkey = ? "
        "ORDER BY e.relative_path, e.entry_id LIMIT ? OFFSET ?",
        [fkey, members_limit, members_offset],
    ).fetchall()
    _hold(catalog, "lineage_open_members", "entry_id", [str(row[0]) for row in rows])
    # Each file's lines, by the way they lead: a derivation leads from its source to its target; a
    # mention leads from the file mentioned (the target) to the file that mentions it (the source).
    touching = {
        str(entry): (int(into), int(out))
        for entry, into, out in catalog.query(
            "SELECT entry_id, sum(is_in), sum(is_out) FROM ("
            "SELECT source_id AS entry_id, "
            "CASE WHEN primitive = 'references' THEN 1 ELSE 0 END AS is_in, "
            "CASE WHEN primitive = 'references' THEN 0 ELSE 1 END AS is_out FROM lineage_ends "
            "UNION ALL SELECT target_id, "
            "CASE WHEN primitive = 'references' THEN 0 ELSE 1 END, "
            "CASE WHEN primitive = 'references' THEN 1 ELSE 0 END "
            "FROM lineage_ends WHERE target_id <> source_id) "
            "WHERE entry_id IN (SELECT entry_id FROM lineage_open_members) GROUP BY entry_id"
        ).fetchall()
    }
    members = [
        FamilyMember(
            id=str(entry),
            name=str(name),
            path=str(path),
            extension=str(extension),
            folder_path=None if folder_path is None else str(folder_path),
            lines=sum(touching.get(str(entry), (0, 0))),
            lines_in=touching.get(str(entry), (0, 0))[0],
            lines_out=touching.get(str(entry), (0, 0))[1],
        )
        for entry, name, path, extension, folder_path in rows
    ]
    lines_total = _count(
        catalog,
        "SELECT count(*) FROM lineage_ends WHERE source_id IN (SELECT entry_id FROM "
        "lineage_family_files WHERE fkey = ?) OR target_id IN (SELECT entry_id FROM "
        "lineage_family_files WHERE fkey = ?)",
        [fkey, fkey],
    )
    drawn = catalog.query(
        "SELECT line_id, source_id, target_id, primitive, proposed, a, b FROM lineage_ends "
        "WHERE source_id IN (SELECT entry_id FROM lineage_family_files WHERE fkey = ?) "
        "OR target_id IN (SELECT entry_id FROM lineage_family_files WHERE fkey = ?) "
        "ORDER BY primitive, line_id LIMIT ?",
        [fkey, fkey, edge_limit],
    ).fetchall()
    lines = [
        FamilyLine(
            id=str(line),
            source=str(source),
            target=str(target),
            kind=_line_kind(str(primitive), proposed=bool(proposed)),
            primitive=str(primitive),
            source_node=_node_id(str(first)),
            target_node=_node_id(str(second)),
        )
        for line, source, target, primitive, proposed, first, second in drawn
    ]
    return OpenFamily(
        id=family,
        label=_words(fkey.partition(_SEP)[2]),
        members_total=total,
        members_in_conflict=_count(
            catalog,
            "SELECT coalesce(sum(n), 0) FROM lineage_family_conflicts WHERE fkey = ?",
            [fkey],
        ),
        members=members,
        members_left_out=max(0, total - members_offset - len(members)),
        members_offset=members_offset,
        lines=lines,
        lines_total=lines_total,
        lines_left_out=max(0, lines_total - len(lines)),
    )


def _rule_state(catalog: Catalog, rules: Sequence[str], folder: str | None) -> FamiliesRule:
    where = " in this folder" if folder else ""
    if not rules:
        return FamiliesRule(
            state="no_rule",
            rule_ids=[],
            families=0,
            files_in_families=0,
            message=(
                "No rule in this run groups names into families, so every file with a line is "
                "drawn as itself."
            ),
        )
    count = _count(catalog, "SELECT count(*) FROM lineage_family_size")
    files = _count(catalog, "SELECT coalesce(sum(members), 0) FROM lineage_family_size")
    if not count:
        return FamiliesRule(
            state="none_formed",
            rule_ids=list(rules),
            families=0,
            files_in_families=0,
            message=f"The rule formed no family of two or more files{where}.",
        )
    return FamiliesRule(
        state="found",
        rule_ids=list(rules),
        families=count,
        files_in_families=files,
        message=f"{count:,} famil{'y' if count == 1 else 'ies'} of {files:,} files{where}.",
    )


def _provenance(catalog: Catalog, folder: str | None) -> FamiliesProvenance:
    by_kind: dict[str, int] = {}
    for primitive, proposed, count in catalog.query(
        "SELECT primitive, proposed, count(*) FROM lineage_ends GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall():
        by_kind[_line_kind(str(primitive), proposed=bool(proposed))] = int(count)
    drawn = sum(by_kind.values())
    inside = _count(catalog, "SELECT count(*) FROM lineage_ends WHERE a = b")
    # A line with one end in the scope and one outside it (both live files), and a line with an end
    # that is no live file at all.
    leaving = _count(
        catalog,
        "SELECT count(*) FROM lineage_lines l JOIN lineage_live s ON s.entry_id = l.source_id "
        "JOIN lineage_live t ON t.entry_id = l.target_id WHERE l.source_id <> l.target_id "
        "AND s.in_scope <> t.in_scope",
    )
    open_ends = _count(
        catalog,
        "SELECT count(*) FROM lineage_lines l WHERE l.source_id NOT IN "
        "(SELECT entry_id FROM lineage_live) OR l.target_id NOT IN "
        "(SELECT entry_id FROM lineage_live)",
    )
    if not drawn:
        said = (
            "No line of the chosen relations joins two files here: nothing is drawn, which is "
            "not evidence that nothing joins them."
        )
    else:
        said = f"{drawn:,} line{'' if drawn == 1 else 's'} between files, from this run's records."
    if folder is None:
        leaving = 0
    return FamiliesProvenance(
        lines=drawn,
        by_kind=by_kind,
        lines_inside_families=inside,
        lines_leaving_scope=leaving,
        lines_to_open_ends=open_ends,
        message=said,
    )


@route("explorer/lineage/families", model=LineageFamiliesResponse)
def families_route(
    context: V1Context, query: Mapping[str, Sequence[str]]
) -> LineageFamiliesResponse:
    return families(context, query)
