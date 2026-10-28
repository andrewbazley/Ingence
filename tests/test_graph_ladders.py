"""Many ladders in the Neighborhood.

A ladder is the chain of levels the Years view climbs: the root, the person and the line of work
where the engine knows them, the experiment, and the file at the foot. A neighborhood touches many:
the center, its folder, what it contains and what is joined to it can lie in different experiments.
On the explorer fixture (``fixtures/explorer_project.py``, invented names): three experiment
folders, each with raw, processed and analysis folders; notes, an archive, scratch files and a deep
folder that no rule reads as an experiment.
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


def _ask(project: Path, center: str, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    asked = {"center": [center], **{name: [str(v)] for name, v in query.items()}}
    return dispatch(context, "graph/neighborhood", asked)


def _hood(explorer: ExplorerProject, path: str, project: Path | None = None, **query: Any):
    status, body = _ask(project or explorer.project, explorer.ids[path], **query)
    assert status == HTTPStatus.OK, body
    from ingence.api.v1.models import RESPONSE_MODELS

    RESPONSE_MODELS["graph_neighborhood"].model_validate(body)
    return body


def _stem() -> str:
    from fixtures.laboratory_pool import STEMS

    return STEMS[0]


def _raw(explorer: ExplorerProject, number: int) -> str:
    return f"{explorer.experiments[0]}/raw/{_stem()}_{number:02d}.nd2"


def _levels(ladder: dict[str, Any]) -> list[str]:
    return [rung["level"] for rung in ladder["rungs"]]


def test_a_neighborhood_inside_one_experiment_stands_on_one_ladder(explorer) -> None:
    body = _hood(explorer, _raw(explorer, 1), include_candidates="false")
    assert body["ladders_total"] == 1
    assert body["ladders_left_out"] == 0
    (ladder,) = body["ladders"]
    # The center, its folder, the files made from it and the script that names it.
    assert len(body["nodes"]) >= 4
    assert {node["ladder"] for node in body["nodes"]} == {ladder["id"]}
    assert ladder["members"] == [node["id"] for node in body["nodes"]]
    assert ladder["is_center_ladder"] is True
    levels = _levels(ladder)
    assert levels == ["root", "experiment"]
    last = ladder["rungs"][-1]
    assert last["id"] == explorer.ids[explorer.experiments[0]]
    assert last["path"] == explorer.experiments[0]
    assert ladder["id"].startswith("ladder:")
    # By default the themes are not read: the ladder says its line of work was not asked for.
    assert ladder["line_of_work"] == "not_computed"
    assert "lines_of_work=true" in ladder["line_of_work_reason"]


def test_the_real_themes_only_add_rungs_and_never_move_a_node(explorer) -> None:
    plain = _hood(explorer, _raw(explorer, 1))
    themed = _hood(explorer, _raw(explorer, 1), lines_of_work="true")
    (before,), (after,) = plain["ladders"], themed["ladders"]
    # The themes ran for real: the line of work is found or its absence is said, and the engine
    # did not fail.
    assert after["line_of_work"] in ("placed", "not_placed"), after["line_of_work_reason"]
    assert (after["line_of_work"] == "placed") == ("line_of_work" in _levels(after))
    # Same ladder, same members, same experiment: only a person and a line of work are added.
    assert after["id"] == before["id"]
    assert after["members"] == before["members"]
    assert after["rungs"][-1] == before["rungs"][-1]
    assert [rung for rung in after["rungs"] if rung["level"] in ("root", "experiment")] == before[
        "rungs"
    ]
    assert [node["ladder"] for node in themed["nodes"]] == [
        node["ladder"] for node in plain["nodes"]
    ]


def test_nested_experiments_give_the_same_ladders_with_or_without_the_themes(
    explorer, tmp_path
) -> None:
    """An experiment a rule resolved inside another is part of it, never a ladder of its own, and
    one it only found in conflict is none. The same reading whether or not the themes are read."""
    from fixtures.explorer_project import AddedClaim, with_published

    first = explorer.experiments[0]
    predicate = _experiment_predicate(explorer, first)
    project = with_published(
        explorer,
        tmp_path,
        claims=(
            AddedClaim(first, predicate, onto=f"{first}/analysis"),
            AddedClaim(first, predicate, onto="notes", conflicting=True),
        ),
    )
    summary = f"{first}/analysis/{_stem()}_summary.csv"
    for lines_of_work in ("false", "true"):
        body = _hood(explorer, summary, project=project, lines_of_work=lines_of_work)
        assert body["ladders_total"] == 1, lines_of_work
        (ladder,) = body["ladders"]
        # The highest experiment, not the nested one the files lie in.
        assert ladder["rungs"][-1]["id"] == explorer.ids[first], lines_of_work
        assert {node["ladder"] for node in body["nodes"]} == {ladder["id"]}
    # A claim found in conflict names no experiment: notes stay on their top folder's ladder.
    for lines_of_work in ("false", "true"):
        body = _hood(explorer, "notes/protocol.md", project=project, lines_of_work=lines_of_work)
        (ladder,) = body["ladders"]
        assert _levels(ladder) == ["root", "folder"], lines_of_work
        assert ladder["line_of_work"] == "no_experiment"


def test_a_line_to_another_experiment_adds_a_ladder_the_centers_first(explorer, tmp_path) -> None:
    from fixtures.explorer_project import with_published
    from fixtures.laboratory_pool import STEMS

    other_mask = f"{explorer.experiments[1]}/processed/{STEMS[1]}_01_mask.tif"
    project = with_published(
        explorer, tmp_path, lines=((_raw(explorer, 1), other_mask, "derives"),)
    )
    body = _hood(explorer, _raw(explorer, 1), project=project)
    assert body["ladders_total"] == 2
    first, second = body["ladders"]
    assert first["is_center_ladder"] is True
    assert second["is_center_ladder"] is False
    assert first["rungs"][-1]["path"] == explorer.experiments[0]
    assert second["rungs"][-1]["path"] == explorer.experiments[1]
    assert second["members"] == [explorer.ids[other_mask]]
    standing = {node["id"]: node["ladder"] for node in body["nodes"]}
    assert standing[explorer.ids[other_mask]] == second["id"]
    assert standing[explorer.ids[_raw(explorer, 1)]] == first["id"]
    # Every node stands on exactly one ladder, and each ladder lists exactly its own nodes.
    for ladder in body["ladders"]:
        assert ladder["members"] == [
            node["id"] for node in body["nodes"] if node["ladder"] == ladder["id"]
        ]
    assert sum(len(ladder["members"]) for ladder in body["ladders"]) == len(body["nodes"])
    # The other ends of the lines are still drawn as before.
    assert any(
        edge["source"] == explorer.ids[_raw(explorer, 1)]
        and edge["target"] == explorer.ids[other_mask]
        for edge in body["edges"]
    )


def test_the_cap_on_ladders_says_how_many_it_left_out_and_never_drops_a_node(
    explorer, tmp_path
) -> None:
    from fixtures.explorer_project import with_published
    from fixtures.laboratory_pool import STEMS

    other_mask = f"{explorer.experiments[1]}/processed/{STEMS[1]}_01_mask.tif"
    project = with_published(
        explorer, tmp_path, lines=((_raw(explorer, 1), other_mask, "derives"),)
    )
    whole = _hood(explorer, _raw(explorer, 1), project=project)
    capped = _hood(explorer, _raw(explorer, 1), project=project, max_ladders=1)
    assert [ladder["id"] for ladder in capped["ladders"]] == [whole["ladders"][0]["id"]]
    assert capped["ladders_total"] == 2
    assert capped["ladders_left_out"] == 1
    # The same nodes and lines; the node on the ladder left out stands on none.
    assert [node["id"] for node in capped["nodes"]] == [node["id"] for node in whole["nodes"]]
    assert capped["edges"] == whole["edges"]
    left_out = next(node for node in capped["nodes"] if node["id"] == explorer.ids[other_mask])
    assert left_out["ladder"] is None


def test_an_experiment_folder_and_what_it_holds_share_one_ladder(explorer) -> None:
    body = _hood(explorer, explorer.experiments[0])
    assert body["ladders_total"] == 1
    (ladder,) = body["ladders"]
    center = explorer.ids[explorer.experiments[0]]
    assert ladder["rungs"][-1]["id"] == center
    assert center in ladder["members"]
    assert len(ladder["members"]) == len(body["nodes"]) >= 4


def test_a_file_in_no_experiment_stands_on_the_ladder_of_its_top_folder(explorer) -> None:
    body = _hood(explorer, "notes/protocol.md")
    (ladder,) = body["ladders"]
    assert _levels(ladder) == ["root", "folder"]
    assert ladder["rungs"][1]["name"] == "notes"
    assert ladder["rungs"][1]["path"] == "notes"
    assert ladder["line_of_work"] == "no_experiment"
    assert "experiment" in str(ladder["line_of_work_reason"])
    # The root carries the label the project gave it, never its location on this machine.
    assert "/" not in ladder["rungs"][0]["name"]


def _experiment_predicate(explorer: ExplorerProject, path: str) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(explorer.project) as catalog:
        found = catalog.query(
            "SELECT min(predicate) FROM claims WHERE subject = ? "
            "AND predicate LIKE 'classifies.experiment_folder%'",
            [explorer.ids[path]],
        ).fetchone()
    assert found is not None and found[0], path
    return str(found[0])


def test_a_folder_above_an_experiment_stands_on_that_experiments_ladder(explorer, tmp_path) -> None:
    from fixtures.explorer_project import AddedClaim, with_published

    first = explorer.experiments[0]
    nested = "deep/a/b/c"
    project = with_published(
        explorer,
        tmp_path,
        claims=(AddedClaim(first, _experiment_predicate(explorer, first), onto=nested),),
    )
    body = _hood(explorer, nested, project=project)
    parent = next(node for node in body["nodes"] if node["placement"] == "parent")
    assert parent["path"] == "deep/a/b"
    # No folder at or above ``deep/a/b`` is an experiment, yet it is no ladder of its own: it
    # holds the experiment and stands on its ladder.
    assert body["ladders_total"] == 1
    (ladder,) = body["ladders"]
    assert parent["ladder"] == ladder["id"]
    assert ladder["rungs"][-1]["id"] == explorer.ids[nested]
    assert ladder["rungs"][-1]["path"] == nested
    assert {node["ladder"] for node in body["nodes"]} == {ladder["id"]}


def test_a_folder_in_no_experiment_and_what_it_holds_stand_on_the_top_folders_ladder(
    explorer,
) -> None:
    body = _hood(explorer, "deep/a")
    (ladder,) = body["ladders"]
    assert _levels(ladder) == ["root", "folder"]
    assert ladder["rungs"][1]["path"] == "deep"
    assert {node["ladder"] for node in body["nodes"]} == {ladder["id"]}
    assert len(body["nodes"]) >= 3  # deep, deep/a, deep/a/b


def test_the_themes_helper_answers_for_every_experiment_the_themes_draw(explorer) -> None:
    from ingence.api.v1 import context_for
    from ingence.themes import experiment_places_or_reason

    run_id = context_for(explorer.project, settled=None, job_running=False).run_pinned
    assert run_id is not None
    places, reason = experiment_places_or_reason(explorer.project, run_id)
    assert reason is None and places
    folders = {explorer.ids[path] for path in explorer.experiments}
    assert set(places) <= folders
    for place in places.values():
        assert set(place) == {"theme_id", "label", "person", "unplaced_reason"}
        assert (place["theme_id"] is None) == (place["label"] is None)
        # A person says why not or is placed, never both.
        assert (place["theme_id"] is None) == (place["unplaced_reason"] is not None)
    # A run that is not one of the project's is said, not raised.
    missing, why = experiment_places_or_reason(
        explorer.project, "run_20200101T000000Z_000000000000"
    )
    assert missing is None and why


def test_lines_of_work_say_where_they_come_from_and_where_they_were_not_found(
    explorer, monkeypatch
) -> None:
    unit = explorer.ids[explorer.experiments[0]]
    path = _raw(explorer, 1)

    def placed(project: Path, run_id: str) -> tuple[dict[str, dict[str, Any]], None]:
        return (
            {
                unit: {
                    "theme_id": 3,
                    "label": "microscopy · confocal",
                    "person": "A. Person",
                    "unplaced_reason": None,
                }
            },
            None,
        )

    monkeypatch.setattr("ingence.themes.experiment_places_or_reason", placed)
    (ladder,) = _hood(explorer, path, lines_of_work="true")["ladders"]
    assert _levels(ladder) == ["root", "person", "line_of_work", "experiment"]
    assert ladder["line_of_work"] == "placed"
    assert ladder["line_of_work_reason"] is None
    person, line = ladder["rungs"][1], ladder["rungs"][2]
    assert person["name"] == "A. Person"
    assert person["id"] is None
    assert line["name"] == "microscopy · confocal"
    assert line["id"] == "3"

    def unplaced(project: Path, run_id: str) -> tuple[dict[str, dict[str, Any]], None]:
        return (
            {
                unit: {
                    "theme_id": None,
                    "label": None,
                    "person": None,
                    "unplaced_reason": "its words are no closer to any other experiment's",
                }
            },
            None,
        )

    monkeypatch.setattr("ingence.themes.experiment_places_or_reason", unplaced)
    (ladder,) = _hood(explorer, path, lines_of_work="true")["ladders"]
    assert _levels(ladder) == ["root", "experiment"]
    assert ladder["line_of_work"] == "not_placed"
    assert ladder["line_of_work_reason"] == "its words are no closer to any other experiment's"

    def missing(project: Path, run_id: str) -> tuple[None, str]:
        return None, "the themes could not run: a package they need is missing"

    monkeypatch.setattr("ingence.themes.experiment_places_or_reason", missing)
    (ladder,) = _hood(explorer, path, lines_of_work="true")["ladders"]
    assert _levels(ladder) == ["root", "experiment"]
    assert ladder["line_of_work"] == "not_computed"
    assert "a package they need is missing" in ladder["line_of_work_reason"]


def test_lines_of_work_are_left_out_unless_asked_for_and_the_ladder_says_so(
    explorer, monkeypatch
) -> None:
    def never(project: Path, run_id: str) -> object:
        message = "the themes were read although the request did not ask for them"
        raise AssertionError(message)

    monkeypatch.setattr("ingence.themes.experiment_places_or_reason", never)
    for query in ({}, {"lines_of_work": "false"}):
        (ladder,) = _hood(explorer, _raw(explorer, 1), **query)["ladders"]
        assert _levels(ladder) == ["root", "experiment"]
        assert ladder["line_of_work"] == "not_computed"
        assert "lines_of_work=true" in ladder["line_of_work_reason"]


def test_a_neighborhood_writes_nothing_unless_the_themes_are_asked_for(explorer, tmp_path) -> None:
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(explorer.project, copy)
    shutil.rmtree(copy / "projections", ignore_errors=True)

    def state() -> list[str]:
        return sorted(str(path.relative_to(copy)) for path in copy.rglob("*") if path.is_file())

    before = state()
    _hood(explorer, _raw(explorer, 1), project=copy)
    assert state() == before
    _hood(explorer, _raw(explorer, 1), project=copy, lines_of_work="true")
    # Asking for the themes builds them and keeps them in the project, the cache the Years view
    # writes.
    assert [path for path in state() if path not in before]


def test_the_answer_is_the_same_every_time_and_the_nodes_and_lines_are_unchanged(explorer) -> None:
    first = _hood(explorer, _raw(explorer, 1))
    second = _hood(explorer, _raw(explorer, 1))
    assert first == second
    # The fields a page drew before the ladders are all still there.
    for node in first["nodes"]:
        assert {"id", "name", "path", "placement", "hop", "is_center"} <= set(node)
    assert first["truncation"]["capped"] is False
    assert {edge["primitive"] for edge in first["edges"]} >= {"contains", "derives"}


def test_a_request_for_a_ladder_count_or_a_switch_that_is_none_is_refused(explorer) -> None:
    for query in ({"max_ladders": 0}, {"max_ladders": 201}, {"lines_of_work": "maybe"}):
        status, body = _ask(explorer.project, explorer.ids[_raw(explorer, 1)], **query)
        assert status == HTTPStatus.BAD_REQUEST, query
        assert body["error"]["parameter"] in ("max_ladders", "lines_of_work")
