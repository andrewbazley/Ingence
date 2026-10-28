"""The Graph workspace's Lineage: forks and joins drawn, nothing invented.

On the explorer fixture (``fixtures/explorer_project.py``, invented names): in the first
experiment, raw 1 -> mask 1 -> quant 1, raw 2 -> mask 2 -> quant 2, raw 1 -> quant 2 (a fork),
quant 1 + quant 2 -> summary (a join), summary -> figure; a script that *mentions* both raw files
and one file that is no entry; one withdrawn derivation; and proposals the engine made itself.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


def _ask(project: Path, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    return dispatch(context, "explorer/lineage", {name: [str(v)] for name, v in query.items()})


def _lineage(explorer: ExplorerProject, path: str, **query: Any) -> dict[str, Any]:
    status, body = _ask(explorer.project, focus=explorer.ids[path], **query)
    assert status == HTTPStatus.OK, body
    return body


def _paths(explorer: ExplorerProject) -> dict[str, str]:
    return {entry: path for path, entry in explorer.ids.items()}


def _pairs(explorer: ExplorerProject, body: dict[str, Any], kind: str) -> set[tuple[str, str]]:
    paths = _paths(explorer)
    return {
        (paths[edge["flow_from"]], paths[edge["flow_to"]])
        for edge in body["edges"]
        if edge["kind"] == kind
    }


def _summary(explorer: ExplorerProject) -> str:
    return next(
        target for source, target in explorer.derivations if target.endswith("_summary.csv")
    )


def test_the_summary_s_lineage_forks_and_joins_exactly_as_published(explorer) -> None:
    body = _lineage(explorer, _summary(explorer), direction="both", depth=4)
    drawn = _pairs(explorer, body, "published_derivation")
    assert drawn == set(explorer.derivations)
    upstream_of_summary = [pair for pair in drawn if pair[1] == _summary(explorer)]
    assert len(upstream_of_summary) == 2  # the join
    raw_one = next(source for source, _ in explorer.derivations if source.endswith("_01.nd2"))
    assert len([pair for pair in drawn if pair[0] == raw_one]) == 2  # the fork
    # Hops: upstream negative, downstream positive, the focus nought.
    hops = {_paths(explorer)[node["id"]]: node["hop"] for node in body["nodes"]}
    assert hops[_summary(explorer)] == 0
    assert all(hop < 0 for path, hop in hops.items() if path.endswith(".nd2"))
    assert all(hop > 0 for path, hop in hops.items() if path.endswith(".pdf"))


def test_a_withdrawn_edge_is_never_drawn(explorer) -> None:
    body = _lineage(explorer, _summary(explorer), direction="both", depth=6)
    pairs = {(edge["flow_from"], edge["flow_to"]) for edge in body["edges"]}
    source, target = explorer.withdrawn
    assert (explorer.ids[source], explorer.ids[target]) not in pairs
    assert "edge_explorer_withdrawn" not in {edge["id"] for edge in body["edges"]}


def test_a_mention_is_drawn_as_a_mention_and_never_as_made_from(explorer) -> None:
    raw_one, _ = explorer.mentions[0][1], None
    body = _lineage(explorer, raw_one, direction="down", depth=1)
    mentions = [edge for edge in body["edges"] if edge["primitive"] == "references"]
    assert mentions
    for edge in mentions:
        assert edge["kind"] == "published_mention"
        # The record's direction is kept: the script is the source, the file mentioned the target.
        assert _paths(explorer)[edge["source"]] == explorer.mentions[0][0]
        # For the layout only, the mention flows from the file mentioned to the script.
        assert edge["flow_from"] == edge["target"]


def test_an_open_end_is_said_and_no_node_is_made_for_it(explorer) -> None:
    from fixtures.explorer_project import UNRESOLVED_TARGET

    script, _ = explorer.unresolved
    body = _lineage(explorer, script, direction="both", depth=1)
    assert UNRESOLVED_TARGET not in {node["id"] for node in body["nodes"]}
    ends = [end for end in body["open_ends"] if end["named"] == UNRESOLVED_TARGET]
    assert len(ends) == 1
    assert ends[0]["reason"] == "not_an_entry"
    assert ends[0]["at"] == explorer.ids[script]


def test_what_is_not_drawn_is_counted_and_a_branch_expands_one_at_a_time(explorer) -> None:
    body = _lineage(explorer, _summary(explorer), direction="up", depth=1)
    quants = [node for node in body["nodes"] if node["name"].endswith("_quant.csv")]
    assert len(quants) == 2
    assert all(node["hidden_up"] >= 1 for node in quants)
    assert body["truncation"]["lines_not_drawn"] > 0
    first = min(quants, key=lambda node: node["name"])
    wider = _lineage(explorer, _summary(explorer), direction="up", depth=1, expand=first["id"])
    names = {node["name"] for node in wider["nodes"]}
    assert any(name.endswith("_01_mask.tif") for name in names)
    # Only the branch asked for: the other quantity's mask is still not drawn.
    assert not any(name.endswith("_02_mask.tif") for name in names)


def test_proposals_appear_only_when_asked_for_and_say_proposed(explorer) -> None:
    raw_one = explorer.mentions[0][1]
    without = _lineage(explorer, raw_one, direction="down", depth=1)
    assert not [edge for edge in without["edges"] if edge["kind"].startswith("proposed")]
    with_them = _lineage(explorer, raw_one, direction="down", depth=1, proposals=1)
    proposed = [edge for edge in with_them["edges"] if edge["kind"].startswith("proposed")]
    assert proposed
    for edge in proposed:
        assert edge["candidate_id"] == edge["id"]
    assert with_them["include_proposals"] is True


def test_too_many_neighbours_one_way_are_gathered_by_folder_and_opened_on_asking(explorer) -> None:
    raw_one = explorer.mentions[0][1]
    body = _lineage(explorer, raw_one, direction="down", depth=1, fan=2)
    gathered = [node for node in body["nodes"] if node["kind"] == "aggregate"]
    assert gathered
    assert sum(node["members"] for node in gathered) >= 3
    for node in gathered:
        assert sum(node["member_kinds"].values()) == node["members"]
    opened = _lineage(
        explorer,
        raw_one,
        direction="down",
        depth=1,
        fan=2,
        open=",".join(n["id"] for n in gathered),
    )
    assert not [node for node in opened["nodes"] if node["kind"] == "aggregate"]
    assert len([n for n in opened["nodes"] if n["kind"] == "file"]) >= 1 + sum(
        node["members"] for node in gathered
    )


def test_an_aggregate_holds_one_kind_of_line_and_says_which(explorer, tmp_path) -> None:
    """Every aggregate holds lines of one kind, its edge says that kind, and opening it draws
    exactly those lines. A folder holding a file made from the focus and a script mentioning it
    is not gathered into one aggregate drawn as two derivations."""
    script, raw_one = explorer.mentions[0]
    figure = next(target for _, target in explorer.derivations if target.endswith(".pdf"))
    assert figure.rsplit("/", 1)[0] == script.rsplit("/", 1)[0]
    from fixtures.explorer_project import with_published

    project = with_published(explorer, tmp_path, lines=((raw_one, figure, "derives"),))
    query = {"focus": [explorer.ids[raw_one]], "direction": ["down"], "depth": ["1"], "fan": ["2"]}
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    status, body = dispatch(context, "explorer/lineage", query)
    assert status == HTTPStatus.OK, body
    gathered = {edge["id"]: edge for edge in body["edges"] if edge["id"].startswith("agg:")}
    kinds_by_folder: dict[str, set[str]] = {}
    for node in body["nodes"]:
        if node["kind"] == "aggregate":
            kinds_by_folder.setdefault(node["folder_id"], set()).add(gathered[node["id"]]["kind"])
    # The script's folder holds one line of each kind: two aggregates, never one "made from".
    analysis = explorer.ids[script.rsplit("/", 1)[0]]
    assert kinds_by_folder[analysis] == {"published_derivation", "published_mention"}
    for identifier, edge in gathered.items():
        status, opened = dispatch(context, "explorer/lineage", {**query, "open": [identifier]})
        assert status == HTTPStatus.OK, opened
        before = {node["id"] for node in body["nodes"]}
        members = {node["id"] for node in opened["nodes"] if node["kind"] == "file"} - before
        drawn = [
            line
            for line in opened["edges"]
            if {line["flow_from"], line["flow_to"]} & members
            and explorer.ids[raw_one] in (line["flow_from"], line["flow_to"])
        ]
        assert {line["kind"] for line in drawn} == {edge["kind"]}, identifier
        assert {line["primitive"] for line in drawn} == {edge["primitive"]}, identifier
        assert len(drawn) == edge["lines"], identifier


def test_a_file_drawn_by_opening_one_aggregate_leaves_every_other_aggregate(
    explorer, tmp_path
) -> None:
    """A file drawn by opening one aggregate is counted by no other aggregate.

    A file joined by a derivation and a mention sits in both aggregates. Every opened
    aggregate's files are drawn before the rest are gathered, so a file is drawn or gathered,
    never both, and each aggregate counts only its own undrawn lines."""
    from fixtures.explorer_project import with_published

    script, raw_one = explorer.mentions[0]
    folder = script.rsplit("/", 1)[0]
    figure = next(target for _, target in explorer.derivations if target.endswith(".pdf"))
    project = with_published(
        explorer, tmp_path, lines=((raw_one, figure, "derives"), (raw_one, script, "derives"))
    )
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    query = {"focus": [explorer.ids[raw_one]], "direction": ["down"], "depth": ["1"], "fan": ["2"]}
    status, body = dispatch(context, "explorer/lineage", query)
    assert status == HTTPStatus.OK, body
    mention = next(
        edge["id"]
        for edge in body["edges"]
        if edge["id"].startswith("agg:")
        and edge["kind"] == "published_mention"
        and f":{explorer.ids[folder]}:" in edge["id"]
    )
    status, opened = dispatch(context, "explorer/lineage", {**query, "open": [mention]})
    assert status == HTTPStatus.OK, opened
    drawn = {node["id"] for node in opened["nodes"] if node["kind"] == "file"}
    assert explorer.ids[script] in drawn
    gathered = [node for node in opened["nodes"] if node["kind"] == "aggregate"]
    lines = {edge["id"]: edge["lines"] for edge in opened["edges"]}
    for node in gathered:
        assert lines[node["id"]] == node["members"], node["name"]
    # Every file one hop down is drawn or gathered, and none is both.
    below = {target for source, target in explorer.derivations if source == raw_one}
    reached = len(below | {figure, script})
    held = len(drawn - {explorer.ids[raw_one]}) + sum(node["members"] for node in gathered)
    assert held == reached


def test_what_is_not_drawn_counts_only_lines_an_expansion_could_draw(explorer, tmp_path) -> None:
    """ "+N" does not count lines drawn inside an aggregate, or lines whose other end is no file
    of the run, which no expansion can ever draw. Such an end that is a folder, or a member
    inside a container, is not called "not an entry of this run"."""
    from ingence.storage.catalog import open_catalog

    script, raw_one = explorer.mentions[0]
    # Every line down from the raw file is drawn, gathered into aggregates: nothing is hidden.
    body = _lineage(explorer, raw_one, direction="down", depth=1, fan=2)
    assert [node for node in body["nodes"] if node["kind"] == "aggregate"]
    focus = next(node for node in body["nodes"] if node["is_focus"])
    assert focus["hidden_down"] == 0
    # The script mentions a folder and a member inside the archive, and a file that is no entry.
    with open_catalog(explorer.project) as catalog:
        member = catalog.query(
            "SELECT min(entry_id) FROM entries WHERE entry_kind <> 'filesystem' AND NOT is_deleted"
        ).fetchone()
    assert member is not None and member[0]
    folder = script.rsplit("/", 2)[0] + "/raw"
    from fixtures.explorer_project import with_published

    project = with_published(
        explorer,
        tmp_path,
        lines=((script, folder, "references"), (script, str(member[0]), "references")),
    )
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    for depth, expand in (("1", ""), ("4", ""), ("1", explorer.ids[script])):
        query = {"focus": [explorer.ids[script]], "direction": ["both"], "depth": [depth]}
        if expand:
            query["expand"] = [expand]
        status, drawn = dispatch(context, "explorer/lineage", query)
        assert status == HTTPStatus.OK, drawn
        node = next(node for node in drawn["nodes"] if node["is_focus"])
        assert (node["hidden_up"], node["hidden_down"]) == (0, 0), (depth, expand)
        reasons = {end["named"]: end["reason"] for end in drawn["open_ends"]}
        assert reasons[explorer.ids[folder]] == "folder"
        assert reasons[str(member[0])] == "inside_a_container"
        assert reasons[explorer.unresolved[1]] == "not_an_entry"


def test_groups_are_memberships_never_provenance(explorer) -> None:
    body = _lineage(explorer, _summary(explorer), direction="both", depth=4, groups=1)
    members = [edge for edge in body["edges"] if edge["kind"] == "group_member"]
    assert body["groups"]
    group_ids = {group["id"] for group in body["groups"]}
    for edge in members:
        assert edge["target"] in group_ids
        assert edge["primitive"] == "equivalent"
        assert edge["id"].startswith("member:")
    for group in body["groups"]:
        assert group["members_total"] >= 2
    # Drawing groups adds no provenance.
    without = _lineage(explorer, _summary(explorer), direction="both", depth=4)
    assert _pairs(explorer, body, "published_derivation") == _pairs(
        explorer, without, "published_derivation"
    )
    # A membership's claim can be read with its rule.
    from ingence.api.v1 import context_for, dispatch

    context = context_for(explorer.project, settled=None, job_running=False)
    status, claim = dispatch(context, "explorer/claim", {"id": [members[0]["id"]]})
    assert status == HTTPStatus.OK, claim
    assert claim["predicate"].startswith("groups.")
    assert claim["rule_id"] == members[0]["rule_id"]


def test_a_folder_starts_from_its_files_that_carry_provenance(explorer) -> None:
    folder = explorer.experiments[0]
    body = _lineage(explorer, folder, direction="both", depth=2)
    assert body["focus_kind"] == "folder"
    focus = next(node for node in body["nodes"] if node["is_focus"])
    assert focus["kind"] == "folder"
    seeds = {_paths(explorer)[seed] for seed in body["seeds"]}
    assert seeds
    assert all(path.startswith(f"{folder}/") for path in seeds)
    marks = {mark["name"]: mark for mark in body["folders"]}
    assert {"raw", "processed", "analysis"} <= set(marks)


def test_a_file_with_no_provenance_is_drawn_alone_and_said_so(explorer) -> None:
    scratch = next(path for path in explorer.ids if path.startswith("scratch/"))
    body = _lineage(explorer, scratch, direction="both", depth=3, proposals=1)
    assert [node["id"] for node in body["nodes"]] == [explorer.ids[scratch]]
    assert body["edges"] == []
    assert "not evidence" in body["provenance"]["message"]


def test_refusals_are_said_in_words(explorer) -> None:
    status, body = _ask(
        explorer.project, focus=explorer.ids[_summary(explorer)], direction="sideways"
    )
    assert status == HTTPStatus.BAD_REQUEST
    assert body["error"]["parameter"] == "direction"
    status, body = _ask(explorer.project, focus="entry_nothing")
    assert status == HTTPStatus.NOT_FOUND
    status, body = _ask(explorer.project)
    assert status == HTTPStatus.BAD_REQUEST
    status, body = _ask(explorer.project, focus=explorer.ids[_summary(explorer)], depth=99)
    assert status == HTTPStatus.BAD_REQUEST
