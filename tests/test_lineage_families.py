"""Lineage at scale: name families collapsed, and the quotient graph.

On the explorer fixture (``fixtures/explorer_project.py``, invented names), the first experiment's
files are put into three name families the way the engine's own rule writes one (a group claim of
the rule whose respect is ``name_family``): the four raw files, the four masks and the four
quantity tables. The published lines are the fixture's: raw 1 -> mask 1 -> quant 1, raw 2 -> mask 2
-> quant 2, raw 1 -> quant 2, quant 1 and quant 2 -> the summary, the summary -> the figure. The
script mentions raw 1 and raw 2 (and one file that is no entry). One derivation is withdrawn. One
more published line, mask 1 -> mask 2, lies inside a family.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject

FAMILY_RULE = "name_family_without_stages"


def _key(*words: str) -> str:
    """A family's key as the grouping rule writes it: its words, as typed components."""
    return json.dumps([["word", word] for word in sorted(words)], separators=(",", ":"))


class _Scene:
    """The fixture, with its first experiment's files in three families."""

    def __init__(self, explorer: ExplorerProject, project: Path) -> None:
        from fixtures.laboratory_pool import STEMS

        self.explorer = explorer
        self.project = project
        first, stem = explorer.experiments[0], STEMS[0]
        self.first = first
        self.raw = [f"{first}/raw/{stem}_{n:02d}.nd2" for n in range(1, 5)]
        self.mask = [f"{first}/processed/{stem}_{n:02d}_mask.tif" for n in range(1, 5)]
        self.quant = [f"{first}/processed/{stem}_{n:02d}_quant.csv" for n in range(1, 5)]
        self.summary = f"{first}/analysis/{stem}_summary.csv"
        self.figure = f"{first}/analysis/{stem}_figure.pdf"
        self.script = f"{first}/analysis/analyse_{stem}.py"

    def ask(self, **query: Any) -> dict[str, Any]:
        from ingence.api.v1 import context_for, dispatch

        context = context_for(self.project, settled=None, job_running=False)
        status, body = dispatch(
            context, "explorer/lineage/families", {name: [str(v)] for name, v in query.items()}
        )
        assert status == HTTPStatus.OK, body
        return body

    def refused(self, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
        from ingence.api.v1 import context_for, dispatch

        context = context_for(self.project, settled=None, job_running=False)
        return dispatch(
            context, "explorer/lineage/families", {name: [str(v)] for name, v in query.items()}
        )

    def named(self, body: dict[str, Any]) -> dict[str, str]:
        """Node identifiers to a readable name: a file's path, a family's label."""
        paths = {entry: path for path, entry in self.explorer.ids.items()}
        names = {node["id"]: node["label"] for node in body["nodes"] if node["kind"] == "family"}
        names.update(
            {node["id"]: paths[node["id"]] for node in body["nodes"] if node["kind"] == "file"}
        )
        return names


def _donor(explorer: ExplorerProject) -> tuple[str, str]:
    """A group claim the fixture already holds: (the path it is about, its predicate)."""
    from ingence.storage.catalog import open_catalog

    with open_catalog(explorer.project) as catalog:
        row = catalog.query(
            "SELECT subject, predicate FROM claims "
            "WHERE predicate LIKE 'groups.same_name_identity%' ORDER BY claim_id LIMIT 1"
        ).fetchone()
    assert row is not None
    paths = {entry: path for path, entry in explorer.ids.items()}
    return paths[str(row[0])], str(row[1])


@pytest.fixture(scope="module")
def scene(explorer_project: ExplorerProject, tmp_path_factory: pytest.TempPathFactory) -> _Scene:
    from fixtures.explorer_project import AddedClaim, with_published

    explorer = explorer_project
    held = _Scene(explorer, explorer.project)
    donor, predicate = _donor(explorer)
    families = (
        (held.raw, _key("kelt", "acquisition")),
        (held.mask, _key("kelt", "mask")),
        (held.quant, _key("kelt", "table")),
    )
    claims = tuple(
        AddedClaim(donor, predicate, value=key, onto=member, group_rule=FAMILY_RULE)
        for members, key in families
        for member in members
    )
    project = with_published(
        explorer,
        tmp_path_factory.mktemp("families"),
        lines=((held.mask[0], held.mask[1], "derives"),),
        claims=claims,
    )
    return _Scene(explorer, project)


def _edges(scene: _Scene, body: dict[str, Any]) -> dict[tuple[str, str, str], int]:
    names = scene.named(body)
    return {
        (names[edge["source"]], names[edge["target"]], edge["kind"]): edge["lines"]
        for edge in body["edges"]
    }


def test_the_route_is_registered_and_its_answer_is_its_declared_model(scene) -> None:
    from ingence.api.v1.models import RESPONSE_MODELS

    body = scene.ask()
    RESPONSE_MODELS["explorer_lineage_families"].model_validate(body)
    assert body["run_id"]
    assert body["folder"] is None
    assert body["relations"] == ["derives", "references"]


def test_each_family_is_one_node_with_its_count_and_a_lone_file_is_a_node_of_its_own(scene) -> None:
    body = scene.ask()
    families = {node["label"]: node for node in body["nodes"] if node["kind"] == "family"}
    assert set(families) == {"acquisition kelt", "kelt mask", "kelt table"}
    # Four files each, whether or not they have a line. Two of each have one.
    for node in families.values():
        assert node["members"] == 4
        assert node["members_with_lines"] == 2
        assert node["rule_id"] == FAMILY_RULE
        assert node["id"].startswith("family:")
        assert node["path"] is None
        # The names of the two files that have a line, in path order.
        assert len(node["examples"]) == 2
    lone = {node["id"]: node for node in body["nodes"] if node["kind"] == "file"}
    assert {
        scene.explorer.ids[path] for path in (scene.summary, scene.figure, scene.script)
    } == set(lone)
    assert all(node["members"] == 1 for node in lone.values())
    # The totals say which nodes are families and which are files in no family, so a sentence
    # never calls a file a family.
    assert body["truncation"]["families_total"] == len(families) == 3
    assert body["truncation"]["files_total"] == len(lone) == 3
    assert body["truncation"]["nodes_total"] == 6
    # A file with no line is in no node: the other two raw files only add to the family's count.
    paths = {entry: path for path, entry in scene.explorer.ids.items()}
    assert not {paths[entry] for entry in lone} & set(scene.raw + scene.mask + scene.quant)


def test_the_quotient_graph_counts_the_member_lines_between_families(scene) -> None:
    body = scene.ask()
    edges = _edges(scene, body)
    derivation = "published_derivation"
    summary, figure, script = scene.summary, scene.figure, scene.script
    assert edges == {
        ("acquisition kelt", "kelt mask", derivation): 2,
        ("kelt mask", "kelt table", derivation): 2,
        ("acquisition kelt", "kelt table", derivation): 1,
        ("kelt table", summary, derivation): 2,
        (summary, figure, derivation): 1,
        # The record's direction: the script mentions the raw files.
        (script, "acquisition kelt", "published_mention"): 2,
    }
    # The line between two masks is no edge: it is counted on the family.
    masks = next(node for node in body["nodes"] if node["label"] == "kelt mask")
    assert masks["lines_inside"] == 1
    assert body["provenance"]["lines_inside_families"] == 1
    assert body["provenance"]["lines"] == 11
    assert body["provenance"]["by_kind"] == {"published_derivation": 9, "published_mention": 2}


def test_a_mention_is_never_a_derivation_and_flows_from_the_file_mentioned(scene) -> None:
    body = scene.ask()
    mentions = [edge for edge in body["edges"] if edge["primitive"] == "references"]
    assert [edge["kind"] for edge in mentions] == ["published_mention"]
    (mention,) = mentions
    script = scene.explorer.ids[scene.script]
    assert mention["source"] == script
    assert mention["flow_to"] == script
    assert mention["flow_from"] == mention["target"]
    for edge in body["edges"]:
        if edge["primitive"] == "derives":
            assert edge["flow_from"] == edge["source"]
            assert edge["flow_to"] == edge["target"]
            assert edge["kind"] == "published_derivation"


def test_a_withdrawn_line_and_an_open_end_are_never_drawn_and_the_open_end_is_counted(
    scene,
) -> None:
    from fixtures.explorer_project import UNRESOLVED_TARGET

    body = scene.ask()
    assert UNRESOLVED_TARGET not in {node["id"] for node in body["nodes"]}
    # The script's mention of a file that is no entry.
    assert body["provenance"]["lines_to_open_ends"] == 1
    # The withdrawn derivation (figure -> raw 3) would be a line from the figure to the raw
    # family. The eleven lines are the published ones.
    figure = scene.explorer.ids[scene.figure]
    raw_family = next(node for node in body["nodes"] if node["label"] == "acquisition kelt")
    assert not [
        edge
        for edge in body["edges"]
        if figure in (edge["source"], edge["target"])
        and raw_family["id"] in (edge["source"], edge["target"])
    ]
    assert body["provenance"]["lines"] == 11


def test_nodes_come_most_lines_first_and_the_same_request_gives_the_same_answer(scene) -> None:
    first, second = scene.ask(), scene.ask()
    assert first["nodes"] == second["nodes"]
    assert first["edges"] == second["edges"]
    counts = [node["lines"] for node in first["nodes"]]
    assert counts == sorted(counts, reverse=True)
    # A line counts once for each node it touches: the masks have 2 + 2 in and out, and 1 inside.
    by_label = {node["label"]: node["lines"] for node in first["nodes"]}
    assert by_label["kelt mask"] == 5
    assert by_label["acquisition kelt"] == 5
    assert by_label["kelt table"] == 5


def test_the_limits_say_how_many_they_left_out_and_never_cut_silently(scene) -> None:
    body = scene.ask(limit=3)
    truncation = body["truncation"]
    assert truncation["capped"] is True
    assert truncation["nodes_total"] == 6
    assert truncation["nodes_left_out"] == 3
    assert (truncation["families_total"], truncation["files_total"]) == (3, 3)
    assert len(body["nodes"]) == 3
    assert {node["label"] for node in body["nodes"]} == {
        "acquisition kelt",
        "kelt mask",
        "kelt table",
    }
    # Between a drawn family and one not drawn: the quantities to the summary, the script's
    # mentions of the raw files.
    assert truncation["lines_to_nodes_not_drawn"] == 4
    assert truncation["edges_total"] == 3
    assert truncation["edges_left_out"] == 0
    assert "3 nodes" in truncation["message"]
    # The whole graph, which the nodes drawn are three of six: six edges, three among the nodes
    # drawn, two between a drawn node and one not drawn (the quantities to the summary, the
    # script's mentions of the raw files: four lines), and one among nodes not drawn (the summary
    # to the figure, one line).
    assert truncation["edges_in_graph"] == 6
    assert truncation["edges_to_nodes_not_drawn"] == 2
    assert truncation["edges_among_nodes_not_drawn"] == 1
    assert truncation["lines_among_nodes_not_drawn"] == 1
    assert (
        truncation["edges_total"]
        + truncation["edges_to_nodes_not_drawn"]
        + truncation["edges_among_nodes_not_drawn"]
        == truncation["edges_in_graph"]
    )
    assert "6 edges" in truncation["message"]
    assert "1 line)" in truncation["message"]
    narrow = scene.ask(limit=3, edge_limit=2)
    assert len(narrow["edges"]) == 2
    assert narrow["truncation"]["edges_total"] == 3
    assert narrow["truncation"]["edges_left_out"] == 1
    # The edges kept are the ones standing for most lines, in a fixed order.
    assert [edge["lines"] for edge in narrow["edges"]] == [2, 2]
    whole = scene.ask()
    assert whole["truncation"]["capped"] is False
    assert whole["truncation"]["nodes_left_out"] == 0
    assert whole["truncation"]["edges_in_graph"] == whole["truncation"]["edges_total"] == 6
    assert whole["truncation"]["edges_among_nodes_not_drawn"] == 0
    assert whole["truncation"]["lines_among_nodes_not_drawn"] == 0


def test_one_family_opens_to_its_files_and_every_line_that_touches_them(scene) -> None:
    body = scene.ask()
    raw_family = next(node for node in body["nodes"] if node["label"] == "acquisition kelt")
    mask_family = next(node for node in body["nodes"] if node["label"] == "kelt mask")
    opened = scene.ask(family=raw_family["id"])["open"]
    assert opened["id"] == raw_family["id"]
    assert opened["label"] == "acquisition kelt"
    assert opened["members_total"] == 4
    assert [member["path"] for member in opened["members"]] == sorted(scene.raw)
    assert opened["members_left_out"] == 0
    by_path = {member["path"]: member["lines"] for member in opened["members"]}
    # Raw 1: to mask 1, to quant 2 and the script's mention. Raw 2: to mask 2 and the mention.
    assert by_path[scene.raw[0]] == 3
    assert by_path[scene.raw[1]] == 2
    assert by_path[scene.raw[2]] == by_path[scene.raw[3]] == 0
    assert opened["lines_total"] == 5
    assert opened["lines_left_out"] == 0
    ids = scene.explorer.ids
    to_mask = [
        line
        for line in opened["lines"]
        if line["source"] == ids[scene.raw[0]] and line["target"] == ids[scene.mask[0]]
    ]
    assert len(to_mask) == 1
    assert to_mask[0]["source_node"] == raw_family["id"]
    assert to_mask[0]["target_node"] == mask_family["id"]
    assert to_mask[0]["kind"] == "published_derivation"
    # The script is no family's: its end says its own identifier.
    mentions = [line for line in opened["lines"] if line["primitive"] == "references"]
    assert len(mentions) == 2
    assert {line["source_node"] for line in mentions} == {ids[scene.script]}
    assert {line["target_node"] for line in mentions} == {raw_family["id"]}


def test_an_opened_family_is_bounded_and_says_what_it_left_out(scene) -> None:
    raw_family = next(node for node in scene.ask()["nodes"] if node["label"] == "acquisition kelt")
    opened = scene.ask(family=raw_family["id"], members_limit=3, edge_limit=2)["open"]
    assert len(opened["members"]) == 3
    assert opened["members_left_out"] == 1
    assert opened["members_total"] == 4
    assert len(opened["lines"]) == 2
    assert opened["lines_total"] == 5
    assert opened["lines_left_out"] == 3


def test_an_opened_family_says_which_lines_lead_to_each_file_and_which_lead_from_it(scene) -> None:
    """In the layout's direction: a derivation leads from the file it was made from, a mention
    leads from the file mentioned."""
    nodes = {node["label"]: node for node in scene.ask()["nodes"]}
    raw = {
        member["path"]: member
        for member in scene.ask(family=nodes["acquisition kelt"]["id"])["open"]["members"]
    }
    # Raw 1: made into mask 1 and quant 2, and mentioned by the script: three lines lead from it.
    assert (raw[scene.raw[0]]["lines_in"], raw[scene.raw[0]]["lines_out"]) == (0, 3)
    assert (raw[scene.raw[1]]["lines_in"], raw[scene.raw[1]]["lines_out"]) == (0, 2)
    assert (raw[scene.raw[2]]["lines_in"], raw[scene.raw[2]]["lines_out"]) == (0, 0)
    masks = {
        member["path"]: member
        for member in scene.ask(family=nodes["kelt mask"]["id"])["open"]["members"]
    }
    # Mask 1 is made from raw 1 (one in), leads to quant 1 and, inside its family, to mask 2.
    assert masks[scene.mask[0]]["lines_in"] == 1
    assert masks[scene.mask[0]]["lines_out"] == 2
    # Mask 2 is made from raw 2 and mask 1 (two in) and leads to quant 2 (one out). Masks 3 and 4
    # have no line at all.
    assert (masks[scene.mask[1]]["lines_in"], masks[scene.mask[1]]["lines_out"]) == (2, 1)
    for number in (2, 3):
        assert (masks[scene.mask[number]]["lines_in"], masks[scene.mask[number]]["lines_out"]) == (
            0,
            0,
        )
    for member in (*raw.values(), *masks.values()):
        assert member["lines"] == member["lines_in"] + member["lines_out"]


def test_an_opened_family_is_read_a_page_at_a_time_and_counts_the_rest(scene) -> None:
    raw_family = next(node for node in scene.ask()["nodes"] if node["label"] == "acquisition kelt")
    first = scene.ask(family=raw_family["id"], members_limit=3)["open"]
    second = scene.ask(family=raw_family["id"], members_limit=3, members_offset=3)["open"]
    assert first["members_offset"] == 0 and second["members_offset"] == 3
    assert [member["path"] for member in first["members"]] == sorted(scene.raw)[:3]
    assert [member["path"] for member in second["members"]] == sorted(scene.raw)[3:]
    assert first["members_left_out"] == 1
    assert second["members_left_out"] == 0
    middle = scene.ask(family=raw_family["id"], members_limit=2, members_offset=1)["open"]
    assert [member["path"] for member in middle["members"]] == sorted(scene.raw)[1:3]
    assert middle["members_left_out"] == 1
    # Past the end is an empty page, not a refusal. A negative offset is refused in words.
    past = scene.ask(family=raw_family["id"], members_offset=50)["open"]
    assert past["members"] == [] and past["members_left_out"] == 0
    status, body = scene.refused(family=raw_family["id"], members_offset=-1)
    assert status == HTTPStatus.BAD_REQUEST
    assert body["error"]["parameter"] == "members_offset"


def test_a_folder_limits_the_scope_and_the_lines_that_leave_it_are_counted(scene) -> None:
    ids = scene.explorer.ids
    # The raw folder holds the four raw files: no line joins two of them.
    inside = scene.ask(folder=ids[f"{scene.first}/raw"])
    assert inside["nodes"] == []
    assert inside["folder_name"] == "raw"
    assert inside["provenance"]["lines"] == 0
    # raw 1 -> mask 1, raw 2 -> mask 2, raw 1 -> quant 2, and the script's two mentions.
    assert inside["provenance"]["lines_leaving_scope"] == 5
    assert "nothing is drawn" in inside["provenance"]["message"]
    # The whole experiment holds every line.
    whole = scene.ask(folder=ids[scene.first])
    assert whole["provenance"]["lines"] == 11
    assert whole["provenance"]["lines_leaving_scope"] == 0
    assert len(whole["nodes"]) == 6
    # Another experiment holds none of these.
    other = scene.ask(folder=ids[scene.explorer.experiments[1]])
    assert other["nodes"] == []
    assert other["provenance"]["lines"] == 0


def test_a_family_id_is_the_same_whatever_the_scope(scene) -> None:
    ids = scene.explorer.ids
    unscoped = {
        node["label"]: node["id"] for node in scene.ask()["nodes"] if node["kind"] == "family"
    }
    scoped = {
        node["label"]: node["id"]
        for node in scene.ask(folder=ids[scene.first])["nodes"]
        if node["kind"] == "family"
    }
    assert unscoped == scoped


def test_the_relations_asked_for_are_the_only_ones_followed(scene) -> None:
    only_derivations = scene.ask(relations="derives")
    assert {edge["primitive"] for edge in only_derivations["edges"]} == {"derives"}
    only_mentions = scene.ask(relations="references")
    assert {edge["primitive"] for edge in only_mentions["edges"]} == {"references"}
    assert {node["id"] for node in only_mentions["nodes"]} >= {scene.explorer.ids[scene.script]}
    status, refused = scene.refused(relations="concerns")
    assert status == HTTPStatus.BAD_REQUEST
    assert refused["error"]["parameter"] == "relations"


def test_proposals_are_added_only_when_asked_and_say_proposed(scene) -> None:
    without = scene.ask()
    assert not [edge for edge in without["edges"] if edge["kind"].startswith("proposed")]
    assert without["include_proposals"] is False
    with_them = scene.ask(proposals=1, limit=1000, edge_limit=5000)
    assert with_them["include_proposals"] is True
    proposed = [edge for edge in with_them["edges"] if edge["kind"].startswith("proposed")]
    assert proposed
    assert with_them["provenance"]["lines"] > without["provenance"]["lines"]
    # What was published is drawn the same with proposals as without.
    published = {
        (edge["source"], edge["target"], edge["kind"]): edge["lines"]
        for edge in with_them["edges"]
        if edge["kind"].startswith("published")
    }
    for edge in without["edges"]:
        assert published[(edge["source"], edge["target"], edge["kind"])] == edge["lines"]


def test_a_run_whose_rule_formed_no_family_draws_every_file_as_itself(explorer_project) -> None:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(explorer_project.project, settled=None, job_running=False)
    status, body = dispatch(context, "explorer/lineage/families", {})
    assert status == HTTPStatus.OK, body
    assert body["families"]["state"] == "none_formed"
    assert body["families"]["families"] == 0
    assert body["nodes"]
    assert {node["kind"] for node in body["nodes"]} == {"file"}
    assert "formed no family" in body["families"]["message"]


def test_the_families_say_how_many_there_are_and_what_forms_them(scene) -> None:
    held = scene.ask()["families"]
    assert held["state"] == "found"
    assert held["rule_ids"] == [FAMILY_RULE]
    assert held["families"] == 3
    assert held["files_in_families"] == 12


def test_a_family_a_folder_or_a_number_that_is_none_is_refused_in_words(scene) -> None:
    status, body = scene.refused(family="family:0000000000000000")
    assert status == HTTPStatus.NOT_FOUND
    assert body["error"]["parameter"] == "family"
    status, body = scene.refused(folder="entry_missing")
    assert status == HTTPStatus.NOT_FOUND
    assert body["error"]["parameter"] == "folder"
    # A file is no folder.
    status, body = scene.refused(folder=scene.explorer.ids[scene.summary])
    assert status == HTTPStatus.NOT_FOUND
    for name, value in (("limit", 0), ("limit", 1001), ("edge_limit", 0), ("members_limit", 0)):
        status, body = scene.refused(**{name: value})
        assert status == HTTPStatus.BAD_REQUEST
        assert body["error"]["parameter"] == name


def test_the_route_reads_the_store_and_writes_nothing(scene) -> None:
    before = sorted(
        (str(path.relative_to(scene.project)), path.stat().st_size)
        for path in scene.project.rglob("*")
        if path.is_file() and "projections" not in path.parts
    )
    scene.ask(proposals=1)
    scene.ask(family=scene.ask()["nodes"][0]["id"])
    after = sorted(
        (str(path.relative_to(scene.project)), path.stat().st_size)
        for path in scene.project.rglob("*")
        if path.is_file() and "projections" not in path.parts
    )
    assert before == after


def test_the_lineage_walk_names_each_file_s_family_as_this_route_does(scene) -> None:
    from ingence.api.v1 import context_for, dispatch

    families = {
        node["label"]: node["id"] for node in scene.ask()["nodes"] if node["kind"] == "family"
    }
    context = context_for(scene.project, settled=None, job_running=False)
    status, walk = dispatch(
        context,
        "explorer/lineage",
        {"focus": [scene.explorer.ids[scene.summary]], "direction": ["up"], "depth": ["3"]},
    )
    assert status == HTTPStatus.OK, walk
    paths = {entry: path for path, entry in scene.explorer.ids.items()}
    by_path = {
        paths[node["id"]]: node["family"] for node in walk["nodes"] if node["kind"] == "file"
    }
    # The summary is in no family. The quantities, masks and raw files are in their own.
    assert by_path[scene.summary] is None
    assert by_path[scene.quant[0]] == families["kelt table"]
    assert by_path[scene.mask[0]] == families["kelt mask"]
    assert by_path[scene.raw[0]] == families["acquisition kelt"]
    # A folder or an aggregate is no family's.
    assert all(node["family"] is None for node in walk["nodes"] if node["kind"] != "file")


def test_where_the_rule_formed_no_family_the_walk_names_none(explorer_project) -> None:
    from ingence.api.v1 import context_for, dispatch

    summary = next(
        target for _, target in explorer_project.derivations if target.endswith("_summary.csv")
    )
    context = context_for(explorer_project.project, settled=None, job_running=False)
    status, walk = dispatch(
        context,
        "explorer/lineage",
        {"focus": [explorer_project.ids[summary]], "direction": ["up"], "depth": ["3"]},
    )
    assert status == HTTPStatus.OK, walk
    assert len(walk["nodes"]) >= 5
    assert {node["family"] for node in walk["nodes"]} == {None}


def test_another_grouping_rule_can_be_named_and_one_that_is_none_is_refused(scene) -> None:
    from ingence.storage.catalog import open_catalog

    with open_catalog(scene.project) as catalog:
        families, files = catalog.query(
            "SELECT count(*), coalesce(sum(n), 0) FROM ("
            "SELECT c.object_reference, count(DISTINCT c.subject) AS n FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'same_name_identity' AND c.predicate LIKE 'groups.%' "
            "AND c.resolution_status IN ('resolved', 'examined_conflicting') "
            "AND NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory' "
            "GROUP BY c.object_reference HAVING count(DISTINCT c.subject) >= 2)"
        ).fetchone()
    body = scene.ask(rules="same_name_identity")
    assert body["families"]["rule_ids"] == ["same_name_identity"]
    assert body["families"]["families"] == families
    assert body["families"]["files_in_families"] == files
    assert {node["rule_id"] for node in body["nodes"] if node["kind"] == "family"} <= {
        "same_name_identity"
    }
    # Naming the default rule is the default.
    assert scene.ask(rules=FAMILY_RULE)["nodes"] == scene.ask()["nodes"]
    status, refused = scene.refused(rules="not_a_rule")
    assert status == HTTPStatus.BAD_REQUEST
    assert refused["error"]["parameter"] == "rules"
    assert "not_a_rule" in refused["error"]["message"]


def test_nodes_with_the_same_number_of_lines_come_by_label_then_identifier(scene) -> None:
    body = scene.ask()
    keys = [(-node["lines"], node["label"], node["id"]) for node in body["nodes"]]
    assert keys == sorted(keys)
    # Three families stand for five lines each: their words decide, as the words say.
    five = [node["label"] for node in body["nodes"] if node["lines"] == 5]
    assert five == ["acquisition kelt", "kelt mask", "kelt table"]
    # A cap that falls inside the tie keeps the first by label, not the first the store holds.
    narrow = scene.ask(limit=2)
    assert [node["label"] for node in narrow["nodes"]] == ["acquisition kelt", "kelt mask"]
    assert narrow["truncation"]["nodes_left_out"] == 4


@pytest.fixture(scope="module")
def conflicted(
    explorer_project: ExplorerProject, tmp_path_factory: pytest.TempPathFactory
) -> _Scene:
    """The first experiment's families again, with some claims in conflict: the quantity tables 3
    and 4 are held in conflict by their family (a person said it was wrong, or a rule defeated
    them), and the summary and the figure share one key only in conflict."""
    from fixtures.explorer_project import AddedClaim, with_published

    explorer = explorer_project
    held = _Scene(explorer, explorer.project)
    donor, predicate = _donor(explorer)

    def claim(member: str, *words: str, conflicting: bool = False) -> AddedClaim:
        return AddedClaim(
            donor,
            predicate,
            value=_key(*words),
            onto=member,
            group_rule=FAMILY_RULE,
            conflicting=conflicting,
        )

    claims = (
        *(claim(member, "kelt", "acquisition") for member in held.raw),
        *(claim(member, "kelt", "mask") for member in held.mask),
        *(claim(member, "kelt", "table") for member in held.quant[:2]),
        *(claim(member, "kelt", "table", conflicting=True) for member in held.quant[2:]),
        claim(held.summary, "kelt", "pair", conflicting=True),
        claim(held.figure, "kelt", "pair", conflicting=True),
    )
    project = with_published(explorer, tmp_path_factory.mktemp("conflicted"), claims=claims)
    return _Scene(explorer, project)


def test_a_claim_in_conflict_makes_no_member_and_is_counted_beside_its_family(conflicted) -> None:
    body = conflicted.ask()
    families = {node["label"]: node for node in body["nodes"] if node["kind"] == "family"}
    # The quantity tables: two files with a resolved claim are the family. Two in conflict are not.
    assert set(families) == {"acquisition kelt", "kelt mask", "kelt table"}
    tables = families["kelt table"]
    assert tables["members"] == 2
    assert tables["members_in_conflict"] == 2
    assert families["acquisition kelt"]["members_in_conflict"] == 0
    # Two files that share a key only in conflict are no family: each is a node of its own.
    lone = {node["id"] for node in body["nodes"] if node["kind"] == "file"}
    ids = conflicted.explorer.ids
    assert {ids[conflicted.summary], ids[conflicted.figure]} <= lone
    assert body["families"]["families"] == 3
    assert body["families"]["files_in_families"] == 4 + 4 + 2
    assert body["truncation"]["families_total"] == 3
    assert body["truncation"]["files_total"] == len(lone)
    opened = conflicted.ask(family=tables["id"])["open"]
    assert opened["members_total"] == 2
    assert opened["members_in_conflict"] == 2
    assert [member["path"] for member in opened["members"]] == sorted(conflicted.quant[:2])
    # A file with no line is in no node, but its family says it is in conflict with it.
    assert all(node["members_in_conflict"] == 0 for node in body["nodes"] if node["kind"] == "file")


def test_the_walk_reads_members_as_the_collapsed_lineage_does(conflicted) -> None:
    from ingence.api.v1.explorer.families import families_of
    from ingence.storage.catalog import open_catalog

    ids = conflicted.explorer.ids
    tables = next(node["id"] for node in conflicted.ask()["nodes"] if node["label"] == "kelt table")
    asked = [
        ids[conflicted.quant[0]],
        ids[conflicted.quant[2]],
        ids[conflicted.summary],
        ids[conflicted.figure],
        ids[conflicted.raw[0]],
    ]
    with open_catalog(conflicted.project) as catalog:
        held = families_of(catalog, asked)
    # Resolved members are named by their family. A claim in conflict names none.
    assert held[ids[conflicted.quant[0]]] == tables
    assert ids[conflicted.quant[2]] not in held
    assert ids[conflicted.summary] not in held
    assert ids[conflicted.figure] not in held
    assert ids[conflicted.raw[0]] in held


def test_files_beneath_an_installed_program_are_no_family_s_members(scene, monkeypatch) -> None:
    """The one definition of a member leaves out a claim about a file beneath a folder raised as
    an installed program's (``review/claim_classes.belongs``)."""
    from ingence.api.v1.explorer.families import families_of
    from ingence.storage.catalog import open_catalog

    ids = scene.explorer.ids
    monkeypatch.setattr(
        "ingence.review.claim_classes.program_regions", lambda catalog: [f"{scene.first}/processed"]
    )
    body = scene.ask()
    families = {node["label"] for node in body["nodes"] if node["kind"] == "family"}
    assert families == {"acquisition kelt"}
    # The masks and the quantity tables keep their lines, each file its own node.
    lone = {node["id"] for node in body["nodes"] if node["kind"] == "file"}
    assert ids[scene.mask[0]] in lone and ids[scene.quant[0]] in lone
    assert body["families"]["files_in_families"] == 4
    with open_catalog(scene.project) as catalog:
        held = families_of(catalog, [ids[scene.raw[0]], ids[scene.mask[0]], ids[scene.quant[0]]])
    assert set(held) == {ids[scene.raw[0]]}
