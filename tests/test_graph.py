"""The first real graph: the dashboard guide's first acceptance test, point by point.

On a small fixture -- a parent folder and two child files, one asserted derivation, one candidate
pair, one unresolved reference and one deleted entry (``fixtures/graph_project.py``) -- the
neighbourhood route draws indexed containment and published assertions as facts, candidates
only on request and labelled proposed, never an unresolved endpoint as a node, and never a
deleted entry; every line opens its evidence; caps are strict, stable and truthful; the store
is read without the source root and without a network; and the response names the run it read,
pinned while a job runs. The page's points (the address surviving a reload, the keyboard list)
are ``test_graph_page.py``'s and the browser harness's.
"""

from __future__ import annotations

import json
import shutil
import socket
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api import v1
from ingence.api.server import DashboardServer
from ingence.api.v1.models import RESPONSE_MODELS, ErrorResponse

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.graph_project import GraphProject


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory) -> GraphProject:
    from fixtures.graph_project import build_graph_project

    return build_graph_project(tmp_path_factory.mktemp("graph"))


def _ask(
    project: Path,
    path: str,
    *,
    settled: str | None = None,
    job_running: bool = False,
    **query: str,
) -> tuple[int, dict[str, Any]]:
    context = v1.context_for(project, settled=settled, job_running=job_running)
    status, payload = v1.dispatch(context, path, {name: [value] for name, value in query.items()})
    return int(status), payload


def _neighborhood(graph: GraphProject, center: str, **query: str) -> dict[str, Any]:
    status, payload = _ask(graph.project, "graph/neighborhood", center=graph.ids[center], **query)
    assert status == 200, payload
    RESPONSE_MODELS["graph_neighborhood"].model_validate(payload)
    return payload


def _named(graph: GraphProject, payload: dict[str, Any]) -> dict[str, str]:
    names = {entry_id: name for name, entry_id in graph.ids.items()}
    return {node["id"]: names.get(node["id"], node["id"]) for node in payload["nodes"]}


def _edges(graph: GraphProject, payload: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    names = _named(graph, payload)
    return {
        (edge["kind"], edge["primitive"], names[edge["source"]], names[edge["target"]])
        for edge in payload["edges"]
    }


# -- containment and the published derivation --------------------------------------------------


def test_a_folder_shows_its_parent_its_live_children_and_the_derivation_between_them(graph):
    payload = _neighborhood(graph, "trell")
    assert payload["center_id"] == graph.ids["trell"]
    assert sorted(_named(graph, payload).values()) == [
        "kelt_processed.csv",
        "kelt_raw.csv",
        "pool",
        "trell",
    ]
    assert _edges(graph, payload) == {
        ("indexed_containment", "contains", "pool", "trell"),
        ("indexed_containment", "contains", "trell", "kelt_raw.csv"),
        ("indexed_containment", "contains", "trell", "kelt_processed.csv"),
        ("published_assertion", "derives", "kelt_raw.csv", "kelt_processed.csv"),
    }
    centre = [node for node in payload["nodes"] if node["is_center"]]
    assert [node["id"] for node in centre] == [graph.ids["trell"]]


def test_the_derivation_keeps_its_direction_and_a_withdrawn_edge_is_never_drawn(graph):
    payload = _neighborhood(graph, "kelt_processed.csv")
    derivations = [edge for edge in payload["edges"] if edge["primitive"] == "derives"]
    assert len(derivations) == 1
    (derivation,) = derivations
    assert derivation["id"] == graph.derivation_edge
    assert derivation["source"] == graph.ids["kelt_raw.csv"]
    assert derivation["target"] == graph.ids["kelt_processed.csv"]
    assert derivation["directed"] is True
    assert derivation["kind"] == "published_assertion"
    assert graph.withdrawn_edge not in {edge["id"] for edge in payload["edges"]}


# -- proposals ---------------------------------------------------------------------------------


def test_a_candidate_appears_only_when_proposals_are_asked_for_and_says_proposed(graph):
    without = _neighborhood(graph, "run_kelt.py")
    assert not [edge for edge in without["edges"] if edge["kind"] == "proposed_candidate"]
    shown = _neighborhood(graph, "run_kelt.py", include_candidates="true")
    proposed = [edge for edge in shown["edges"] if edge["kind"] == "proposed_candidate"]
    assert {(edge["primitive"], _named(graph, shown)[edge["target"]]) for edge in proposed} == {
        ("references", "kelt_raw.csv"),
        ("references", "kelt_processed.csv"),
    }
    for edge in proposed:
        assert "proposed" in edge["label"]
        assert edge["primitive"] != "derives", "a mention is never drawn as a derivation"
        assert edge["candidate_id"] == edge["id"]


def test_a_candidate_promoted_to_the_published_edge_is_drawn_once_as_published(graph):
    from ingence.storage.catalog import open_catalog

    # Two rules propose the pair (a name identity, and one file at two steps): the
    # published line wins over both, whichever of them it was promoted from.
    with open_catalog(graph.project) as catalog:
        (rules,) = catalog.query(
            "SELECT count(DISTINCT rule_id) FROM candidates WHERE primitive = 'derives' "
            "AND source_id = ? AND target_id = ?",
            [graph.ids["kelt_raw.csv"], graph.ids["kelt_processed.csv"]],
        ).fetchone()
    assert rules == 2
    payload = _neighborhood(graph, "kelt_processed.csv", include_candidates="true")
    derivations = [edge for edge in payload["edges"] if edge["primitive"] == "derives"]
    assert [edge["kind"] for edge in derivations] == ["published_assertion"]
    assert derivations[0]["candidate_id"] == graph.promoted_candidate


# -- what could not be connected ---------------------------------------------------------------


def test_an_unresolved_reference_is_explained_and_no_node_is_made_for_it(graph):
    from fixtures.graph_project import UNRESOLVED_TARGET

    payload = _neighborhood(graph, "run_kelt.py", include_candidates="true")
    assert UNRESOLVED_TARGET not in {node["id"] for node in payload["nodes"]}
    assert UNRESOLVED_TARGET not in {edge["target"] for edge in payload["edges"]}
    unresolved = payload["availability"]["unresolved"]
    assert unresolved["count"] == 1
    (item,) = unresolved["items"]
    assert item["edge_id"] == graph.unresolved_edge
    assert item["named"] == UNRESOLVED_TARGET
    assert item["reference_resolution"] == "unresolved"
    # The file's own mentions, resolved again by the engine's resolver from the Index alone:
    # two name files beside it, and one names nothing.
    mentions = payload["availability"]["mentions"]
    assert mentions["total"] == 3
    assert mentions["by_outcome"]["no_match"] == 1
    assert sum(mentions["by_outcome"].values()) == 3


def test_a_deleted_entry_is_absent_and_is_refused_as_a_centre(graph):
    payload = _neighborhood(graph, "trell")
    assert graph.ids["zib_old.txt"] not in {node["id"] for node in payload["nodes"]}
    status, refused = _ask(graph.project, "graph/neighborhood", center=graph.ids["zib_old.txt"])
    assert status == 404
    assert ErrorResponse.model_validate(refused).error.code == "entry_deleted"


# -- every line opens its evidence -------------------------------------------------------------


def test_selecting_the_derivation_shows_its_rule_version_tier_and_evidence(graph):
    status, payload = _ask(graph.project, "graph/edge", id=graph.derivation_edge)
    assert status == 200, payload
    detail = RESPONSE_MODELS["graph_edge"].model_validate(payload)
    assert detail.kind == "published_assertion"
    assert detail.primitive == "derives"
    assert detail.rule_id and detail.rule_version
    assert detail.tier == "defeasible"
    assert detail.source.id == graph.ids["kelt_raw.csv"]
    assert detail.target is not None
    assert detail.target.id == graph.ids["kelt_processed.csv"]
    assert detail.candidate_id == graph.promoted_candidate
    assert detail.records, "the records it rests on are named"
    assert detail.level == "file"
    assert detail.quotient_origin == "native"
    assert "was produced from" in detail.reading
    assert detail.reading.startswith("Published")


def test_selecting_containment_says_indexed_directory(graph):
    payload = _neighborhood(graph, "trell")
    containment = next(
        edge
        for edge in payload["edges"]
        if edge["kind"] == "indexed_containment" and edge["target"] == graph.ids["kelt_raw.csv"]
    )
    status, detail = _ask(graph.project, "graph/edge", id=containment["id"])
    assert status == 200, detail
    assert "indexed directory" in detail["reading"]
    assert detail["rule_id"] == "census.filesystem_containment"
    assert detail["tier"] == "entailed"


def test_selecting_a_proposed_pair_names_its_decision_tier_and_the_review_route(graph):
    shown = _neighborhood(graph, "run_kelt.py", include_candidates="true")
    proposed = next(edge for edge in shown["edges"] if edge["kind"] == "proposed_candidate")
    status, detail = _ask(graph.project, "graph/edge", id=proposed["id"])
    assert status == 200, detail
    assert detail["kind"] == "proposed_candidate"
    assert detail["tier"] == "circumstantial"
    assert detail["review"]["view"] == "candidates"
    assert "proposed" in detail["reading"]


# -- caps --------------------------------------------------------------------------------------


def test_caps_give_a_stable_answer_and_say_exactly_what_was_left_out(graph):
    first = _neighborhood(graph, "trell", max_nodes="2", max_edges="1")
    again = _neighborhood(graph, "trell", max_nodes="2", max_edges="1")
    assert first == again, "the same request draws the same graph"
    assert len(first["nodes"]) == 2
    assert len(first["edges"]) <= 1
    truncation = first["truncation"]
    assert truncation["capped"] is True
    assert truncation["max_nodes"] == 2
    assert truncation["nodes_left_out"] == 2
    assert truncation["count_unknown"] is False
    assert "2" in truncation["message"]
    uncapped = _neighborhood(graph, "trell")
    assert uncapped["truncation"]["capped"] is False
    assert uncapped["truncation"]["nodes_left_out"] == 0


@pytest.mark.parametrize(
    ("query", "parameter"),
    [
        ({"relations": "contains,related_to"}, "relations"),
        ({"hops": "2"}, "hops"),
        ({"hops": "zero"}, "hops"),
        ({"max_nodes": "0"}, "max_nodes"),
        ({"max_nodes": "100000"}, "max_nodes"),
        ({"max_edges": "-1"}, "max_edges"),
        ({"include_candidates": "perhaps"}, "include_candidates"),
    ],
)
def test_an_unknown_relation_or_a_limit_out_of_range_is_refused_in_words(graph, query, parameter):
    status, payload = _ask(graph.project, "graph/neighborhood", center=graph.ids["trell"], **query)
    assert status == 400
    refused = ErrorResponse.model_validate(payload)
    assert refused.error.code == "bad_parameter"
    assert refused.error.parameter == parameter


def test_a_centre_or_a_run_the_project_does_not_hold_is_refused(graph):
    status, payload = _ask(graph.project, "graph/neighborhood")
    assert status == 400
    assert payload["error"]["parameter"] == "center"
    status, payload = _ask(graph.project, "graph/neighborhood", center="entry_nothing")
    assert status == 404
    assert payload["error"]["code"] == "not_found"
    status, payload = _ask(
        graph.project,
        "graph/neighborhood",
        center=graph.ids["trell"],
        run="run_20260101T000009Z_999999999999",
    )
    assert status == 404
    assert payload["error"]["code"] == "unknown_run"
    status, payload = _ask(graph.project, "graph/edge", id="edge_nothing")
    assert status == 404


# -- offline -----------------------------------------------------------------------------------


def test_the_graph_is_read_from_the_store_with_the_source_gone_and_no_network(graph, monkeypatch):
    before = _neighborhood(graph, "trell", include_candidates="true")
    reached: list[object] = []
    real_connect = socket.socket.connect

    def refuse(self: socket.socket, address: object) -> object:
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            reached.append(address)
            message = f"the graph reached for {address}"
            raise OSError(message)
        return real_connect(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", refuse)
    moved = graph.pool.with_name("pool-unplugged")
    shutil.move(graph.pool, moved)
    try:
        after = _neighborhood(graph, "trell", include_candidates="true")
        status, _ = _ask(graph.project, "graph/edge", id=graph.derivation_edge)
    finally:
        shutil.move(moved, graph.pool)
    assert status == 200
    assert after["nodes"] == before["nodes"] and after["edges"] == before["edges"]
    assert after["availability"]["reads_source"] is False
    assert not reached


# -- the run it read ---------------------------------------------------------------------------


def test_a_new_published_run_is_named_and_a_running_job_keeps_the_settled_graph(graph, tmp_path):
    from fixtures.graph_project import LATER_RUN, PUBLISHED_RUN, publish_with

    project = tmp_path / "project"
    shutil.copytree(graph.project, project)
    status, before = _ask(project, "graph/neighborhood", center=graph.ids["trell"])
    assert status == 200
    assert before["run_id"] == PUBLISHED_RUN
    publish_with(project, LATER_RUN, [])
    status, after = _ask(project, "graph/neighborhood", center=graph.ids["trell"])
    assert after["run_id"] == LATER_RUN
    assert after["pinned"]["job_running"] is False
    # While a job that will publish runs, the settled run is served, and said to be.
    status, pinned = _ask(
        project,
        "graph/neighborhood",
        center=graph.ids["trell"],
        settled=PUBLISHED_RUN,
        job_running=True,
    )
    assert status == 200
    assert pinned["run_id"] == PUBLISHED_RUN
    assert pinned["pinned"]["job_running"] is True
    assert pinned["pinned"]["run_current"] == LATER_RUN
    assert "settled" in pinned["pinned"]["message"]
    # An earlier published run can be named, and says it is not the one reads are pinned to.
    status, earlier = _ask(
        project, "graph/neighborhood", center=graph.ids["trell"], run=PUBLISHED_RUN
    )
    assert status == 200
    assert earlier["run_id"] == PUBLISHED_RUN
    assert earlier["pinned"]["is_pinned_run"] is False


# -- over HTTP ---------------------------------------------------------------------------------


def test_the_route_answers_over_the_loopback_server(graph):
    server = DashboardServer(graph.project, port=0)
    server.start()
    try:
        url = (
            server.url.rstrip("/")
            + f"/api/v1/graph/neighborhood?center={graph.ids['trell']}&include_candidates=true"
        )
        with urllib.request.urlopen(url) as response:
            payload = json.loads(response.read())
        assert payload["center_id"] == graph.ids["trell"]
        try:
            urllib.request.urlopen(server.url.rstrip("/") + "/api/v1/graph/neighborhood?hops=9")
        except urllib.error.HTTPError as error:
            assert error.code == 400
        else:  # pragma: no cover - the refusal is the point
            raise AssertionError("an out-of-range request was answered")
    finally:
        server.stop()


# -- what the chosen relations leave out -------------------------------------------------------


def test_published_relations_and_proposals_the_filters_hide_are_counted_beside_the_drawing(graph):
    hidden = _neighborhood(graph, "kelt_raw.csv", relations="contains")
    assert hidden["availability"]["not_drawn"]["published"] == {"derives": 1}
    # The script's proposed mention of it is a references pair, and a describing file naming it and
    # the processed file proposes the two related, whatever their times: neither
    # references nor concerns is chosen.
    assert hidden["availability"]["not_drawn"]["proposed"] == 2
    script = _neighborhood(graph, "run_kelt.py")
    assert script["availability"]["not_drawn"]["proposed"] == 2, "Proposals are off"
    shown = _neighborhood(graph, "run_kelt.py", include_candidates="true")
    assert shown["availability"]["not_drawn"] == {"published": {}, "proposed": 0}


def test_a_count_past_its_budget_is_said_to_be_unknown_never_guessed(graph, monkeypatch):
    from ingence.api.v1 import graph as graph_module

    monkeypatch.setattr(graph_module._Reader, "totals", lambda self, centre_row: None)
    payload = _neighborhood(graph, "trell", max_nodes="2")
    truncation = payload["truncation"]
    assert truncation["count_unknown"] is True
    assert truncation["nodes_left_out"] is None and truncation["edges_left_out"] is None
    assert truncation["capped"] is True
    assert "no number" in truncation["message"]


def test_the_count_budget_interrupts_a_query_that_outlives_it() -> None:
    import duckdb

    from ingence.api.v1.graph import _Budget

    class Held:
        connection = duckdb.connect(":memory:")

    endless = f"SELECT count(*) FROM range({10**12}) a"  # far longer than the budget
    with pytest.raises(duckdb.InterruptException), _Budget(Held(), 0.2) as budget:  # type: ignore[arg-type]
        Held.connection.execute(endless).fetchone()
    assert budget.fired


# -- further cases -----------------------------------------------------------------------------


def _centre_row(graph, name: str) -> dict:
    from ingence.storage.catalog import open_catalog

    with open_catalog(graph.project) as catalog:
        cursor = catalog.query("SELECT * FROM entries WHERE entry_id = ?", [graph.ids[name]])
        columns = [column[0] for column in cursor.description]
        return dict(zip(columns, cursor.fetchone(), strict=True))


@pytest.mark.parametrize(
    "outcome", ["deferred", "content_error", "skipped_by_policy", "changed_during_read"]
)
def test_a_file_whose_content_was_not_read_is_never_said_to_mention_nothing(graph, outcome):
    """Unexamined is not "examined and found no path"."""
    from ingence.api.v1.graph import _mentions
    from ingence.storage.catalog import open_catalog

    centre = {**_centre_row(graph, "kelt_raw.csv"), "scan_outcome": outcome}
    with open_catalog(graph.project) as catalog:
        summary = _mentions(catalog, graph.project, centre)
    assert summary is not None
    assert summary.content_read is False
    assert summary.state == "not_read"
    assert summary.total == 0
    assert outcome.replace("_", " ") in (summary.not_resolved_because or "")


def test_a_file_read_by_no_reader_that_looks_for_paths_records_no_mention(graph):
    payload = _neighborhood(graph, "zorn_scan.bin")
    mentions = payload["availability"]["mentions"]
    assert mentions["content_read"] is True
    assert mentions["state"] == "reader_does_not_look"
    assert "do not look for paths" in mentions["not_resolved_because"]


def test_only_a_reader_that_looks_for_paths_can_say_a_file_mentions_none(graph):
    payload = _neighborhood(graph, "brisa_notes.txt")
    mentions = payload["availability"]["mentions"]
    assert mentions["state"] == "none_found"
    assert mentions["total"] == 0
    script = _neighborhood(graph, "run_kelt.py")["availability"]["mentions"]
    assert script["state"] == "resolved_again"


def test_the_entry_route_serves_the_run_the_graph_names(graph):
    from fixtures.graph_project import PUBLISHED_RUN

    status, payload = _ask(graph.project, "entry", id=graph.ids["trell"], run=PUBLISHED_RUN)
    assert status == 200
    assert payload["run_id"] == PUBLISHED_RUN
    status, payload = _ask(
        graph.project, "entry", id=graph.ids["trell"], run="run_20260101T000009Z_999999999999"
    )
    assert status == 404
    assert payload["error"]["code"] == "unknown_run"


def test_prefix_mappings_are_read_only_up_to_the_run_s_record_watermark(graph):
    from ingence.api.v1.graph import _prefix_mappings

    mappings, said = _prefix_mappings(graph.project, None)
    assert mappings == ()
    assert "watermark" in said
    mappings, said = _prefix_mappings(graph.project, 0)
    assert mappings == ()
    assert "0" in said


def test_a_promoted_candidate_is_explained_as_the_published_edge_it_became(graph):
    status, detail = _ask(graph.project, "graph/edge", id=graph.promoted_candidate)
    assert status == 200, detail
    assert detail["kind"] == "published_assertion"
    assert detail["id"] == graph.derivation_edge
    assert detail["candidate_id"] == graph.promoted_candidate
    assert detail["review"] is None
    assert "nobody has asserted" not in detail["reading"]


def test_the_edge_cap_keeps_the_published_line_that_brought_a_node_in(graph):
    """``max_edges=2`` must not keep two containment lines and drop the derivation."""
    payload = _neighborhood(graph, "kelt_processed.csv", max_edges="2")
    drawn = {node["id"] for node in payload["nodes"]}
    kinds = [edge["kind"] for edge in payload["edges"]]
    assert "published_assertion" in kinds
    for edge in payload["edges"]:
        assert edge["source"] in drawn and edge["target"] in drawn
    related = [node["id"] for node in payload["nodes"] if node["placement"] == "related"]
    for identifier in related:
        assert any(identifier in (edge["source"], edge["target"]) for edge in payload["edges"]), (
            "a related node is drawn with the line that brought it in"
        )


def test_caps_are_said_to_be_reached_only_when_something_was_left_out(graph, monkeypatch):
    from ingence.api.v1 import graph as graph_module

    monkeypatch.setattr(graph_module._Reader, "totals", lambda self, centre_row: None)
    exact = _neighborhood(graph, "trell", max_nodes="4", max_edges="4")
    assert len(exact["nodes"]) == 4
    assert exact["truncation"]["capped"] is False, "nothing was left out at exactly the cap"
    over = _neighborhood(graph, "trell", max_nodes="3", max_edges="4")
    assert over["truncation"]["capped"] is True


def test_any_live_folder_or_file_can_be_found_by_its_path_or_identifier(graph):
    status, payload = _ask(graph.project, "graph/find", q="KELT_RAW")
    assert status == 200, payload
    assert [match["path"] for match in payload["matches"]] == ["trell/kelt_raw.csv"]
    status, payload = _ask(graph.project, "graph/find", q=graph.ids["zorn_scan.bin"])
    assert [match["id"] for match in payload["matches"]] == [graph.ids["zorn_scan.bin"]]
    status, payload = _ask(graph.project, "graph/find", q="zib_old")
    assert payload["matches"] == [], "a deleted entry is not offered"
    status, payload = _ask(graph.project, "graph/find", q="%")
    assert payload["matches"] == [], "a wildcard is matched as itself"
    status, payload = _ask(graph.project, "graph/find", q="kelt", limit="1")
    assert len(payload["matches"]) == 1 and payload["more"] is True
    status, payload = _ask(graph.project, "graph/find", q="")
    assert status == 400
