"""The experiment map's edges. Every name is invented.

Four experiments of one person: two repeats of one imaging experiment (the same words, the same
instrument, different days), a third sharing a stain word and the instrument but not the
rest, and a fourth holding a byte-for-byte copy of one of the first's tables. The map joins each
pair by every kind of overlap it shows, with the evidence, and weighs the edge by what the two
share and how rare it is.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"
INFER = "run_20260101T000003Z_333333333333"
FIRST = "mira/2024-02-05_kelt_dapi_staurosporine"
AGAIN = "mira/2024-02-19_kelt_dapi_staurosporine"
THIRD = "mira/2024-03-11_zeph_dapi_gfp"
COPIED = "mira/2024-04-02_vorn_plates"
TABLE = "a,b\n1,2\n3,4\n"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("e4_connections")
    pool = base / "pool"
    for folder in (FIRST, AGAIN):
        for number in range(1, 4):
            _write(pool / folder / "raw" / f"kelt_dapi_staurosporine_{number:03d}.nd2")
        _write(pool / folder / "analysis" / "kelt_dapi_staurosporine_001_results.csv", TABLE)
    for number in range(1, 4):
        _write(pool / THIRD / "raw" / f"zeph_dapi_gfp_{number:03d}.nd2")
    _write(pool / THIRD / "analysis" / "zeph_dapi_gfp_001_results.csv", "c,d\n5,6\n")
    _write(pool / COPIED / "plates" / "vorn_plate_01.xlsx")
    _write(pool / COPIED / "plates" / "kelt_dapi_staurosporine_001_results.csv", TABLE)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    # A dive, so the copy rules have digests to form their classes from.
    run_deepen(created, run_id=DIVE, started_at="t", profile="deep")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def _ids(project: Project) -> dict[str, str]:
    from ingence.experiment_units import policy_experiments

    with open_catalog(project.path) as catalog:
        return {held["path"]: unit for unit, held in policy_experiments(catalog).items()}


def _ask(project: Project, path: str, **query: Any) -> tuple[HTTPStatus, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project.path, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(value)] for name, value in query.items()})


def _edges(body: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(edge["a"], edge["b"]): edge for edge in body["connections"]["edges"]}


def test_the_map_joins_repeats_shared_words_instruments_and_copies(project: Project) -> None:
    ids = _ids(project)
    assert set(ids) == {FIRST, AGAIN, THIRD, COPIED}
    status, body = _ask(project, "overlap", mode="connections")
    assert status == HTTPStatus.OK, body
    assert body["mode"] == "connections" and body["by_year"] is None
    nodes = {node["experiment_id"]: node for node in body["connections"]["nodes"]}
    assert set(nodes) == set(ids.values())
    assert nodes[ids[FIRST]]["repeats"] == 2 and nodes[ids[THIRD]]["repeats"] == 0
    assert nodes[ids[FIRST]]["area"] == "mira" and nodes[ids[FIRST]]["area_kind"] == "person"
    assert nodes[ids[FIRST]]["start"] == "2024-02-05"
    edges = _edges(body)

    def edge(first: str, second: str) -> dict[str, Any]:
        pair = (ids[first], ids[second]) if ids[first] < ids[second] else (ids[second], ids[first])
        assert pair in edges, (first, second)
        return edges[pair]

    repeat = edge(FIRST, AGAIN)
    assert {"repeat_of", "shared_words", "instrument"} <= set(repeat["kinds"])
    by_kind = {item["kind"]: item for item in repeat["evidence"]}
    assert by_kind["instrument"]["words"] == ["nd2"]
    assert "dapi" in by_kind["shared_words"]["words"]
    assert by_kind["shared_words"]["count"] == len(by_kind["shared_words"]["words"])
    assert by_kind["repeat_of"]["count"] is None
    third = edge(FIRST, THIRD)
    assert "repeat_of" not in third["kinds"]
    assert {"shared_words", "instrument"} <= set(third["kinds"])
    assert {item["kind"]: item for item in third["evidence"]}["shared_words"]["words"] == ["dapi"]
    copied = edge(FIRST, COPIED)
    assert "copy" in copied["kinds"]
    by_copy = {item["kind"]: item for item in copied["evidence"]}
    assert by_copy["copy"]["count"] >= 1 and by_copy["copy"]["unit"] == "files"
    assert by_kind["shared_words"]["unit"] == "words"
    assert by_kind["repeat_of"]["unit"] == "experiments"
    assert all(item["from"] is None and item["to"] is None for item in copied["evidence"])
    for held in edges.values():
        for item in held["evidence"]:
            assert item["unit"] in {"files", "words", "experiments"}
            if item["kind"] == "made_from":
                assert {item["from"], item["to"]} == {held["a"], held["b"]}
    # The repeats share more, and rarer, than the third shares with either: the edge weighs more.
    assert 0.0 < third["weight"] < repeat["weight"] <= 1.0
    assert all(0.0 <= held["weight"] <= 1.0 for held in edges.values())
    assert all(
        held["kinds"] and len(held["evidence"]) == len(held["kinds"]) for held in edges.values()
    )


def test_the_projection_lists_each_pair_once_and_the_by_year_mode_still_answers(
    project: Project,
) -> None:
    from ingence.completeness import completeness_of_run
    from ingence.grouping.connections import connections

    found = completeness_of_run(project.path, INFER)
    with open_catalog(project.path) as catalog:
        nodes, edges = connections(catalog, found.experiments)
    assert len(nodes) == 4
    assert len({(edge.a, edge.b) for edge in edges}) == len(edges)
    assert all(edge.a < edge.b for edge in edges)
    status, body = _ask(project, "overlap", mode="by_year")
    assert status == HTTPStatus.OK, body
    assert body["connections"] is None and body["by_year"] is not None
