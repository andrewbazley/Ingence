"""Two to four groups compared: ``GET /api/v1/groups/compare``.

On the invented theme pool (``fixtures.theme_pool``: six planted lines of work over dated
experiment folders, words that bind to nothing), served by the Dashboard's own server: a group is a
set of words of the lattice (``words:<terms>``) or a line of work (``line:<number>``). The
comparison gives the words in all of the groups and only in each, the experiments in all, in each
only and in either, and the files of each, every count beside what it is out of. Its sets are the
lattice's own, and its file counts are the Map's own: both are checked against the routes the
pages already read.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.theme_pool import build_theme_project
from ingence.api.server import DashboardServer
from ingence.api.v1.groups import GroupCompare

if TYPE_CHECKING:
    from collections.abc import Iterator

pytest.importorskip("sklearn", reason="lines of work need the themes extra")


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[DashboardServer]:
    project, _ = build_theme_project(tmp_path_factory.mktemp("compare") / "built")
    with DashboardServer(project, port=0) as server:
        yield server


def _get(
    server: DashboardServer, path: str, groups: list[str] | None = None
) -> tuple[int, dict[str, Any]]:
    query = urllib.parse.urlencode([("group", group) for group in groups or []])
    url = server.url.rstrip("/") + "/api/v1/" + path + (f"?{query}" if query else "")
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        error.close()
        return error.code, payload


def _sets(server: DashboardServer) -> dict[tuple[str, ...], dict[str, Any]]:
    status, shown = _get(server, "groups/lattice")
    assert status == 200, shown
    return {tuple(found["terms"]): found for found in shown["sets"]}


def _two_sets_that_overlap(server: DashboardServer) -> tuple[dict[str, Any], dict[str, Any]]:
    """Two sets of the lattice that share some experiments and not all of either's."""
    found = list(_sets(server).values())
    for first in found:
        for second in found:
            shared = set(first["experiments"]) & set(second["experiments"])
            if (
                first["terms"] < second["terms"]
                and shared
                and shared != set(first["experiments"])
                and shared != set(second["experiments"])
                and not set(first["terms"]) <= set(second["terms"])
            ):
                return first, second
    pytest.fail("the invented pool should hold two sets of words that overlap in part")


def _spec(found: dict[str, Any]) -> str:
    return "words:" + "|".join(found["terms"])


def test_two_sets_of_words_are_compared_by_words_experiments_and_files(
    served: DashboardServer,
) -> None:
    first, second = _two_sets_that_overlap(served)
    status, shown = _get(served, "groups/compare", [_spec(first), _spec(second)])
    assert status == 200, shown
    GroupCompare.model_validate(shown)
    a, b = shown["groups"]
    assert (a["tag"], b["tag"]) == ("A", "B")
    assert (a["kind"], b["kind"]) == ("words", "words")
    assert a["words"] == sorted(first["terms"]) and b["words"] == sorted(second["terms"])
    # The experiments are the lattice's own, each set's, by folder path.
    assert [e["label"] for e in a["experiments"]] == sorted(first["experiments"])
    assert [e["label"] for e in b["experiments"]] == sorted(second["experiments"])
    only_a = set(first["experiments"]) - set(second["experiments"])
    shared = set(first["experiments"]) & set(second["experiments"])
    either = set(first["experiments"]) | set(second["experiments"])
    assert {e["label"] for e in a["experiments_only"]} == only_a
    assert {e["label"] for e in shown["experiments_in_all"]} == shared
    assert shown["experiments_in_either"] == len(either)
    # Words: the common ones, the ones only each has, and the count they are out of.
    assert shown["words_in_all"] == sorted(set(first["terms"]) & set(second["terms"]))
    assert a["words_only"] == sorted(set(first["terms"]) - set(second["terms"]))
    assert b["words_only"] == sorted(set(second["terms"]) - set(first["terms"]))
    assert shown["words_in_either"] == len(set(first["terms"]) | set(second["terms"]))
    # Files: the sum of the experiments' own, and what they are out of.
    assert a["files"] == sum(e["files"] for e in a["experiments"])
    assert shown["files_in_all"] == sum(e["files"] for e in shown["experiments_in_all"])
    assert shown["files_in_either"] <= a["files"] + b["files"]
    assert shown["files_in_either"] == a["files"] + b["files"] - shown["files_in_all"]
    (pair,) = shown["pairs"]
    assert (pair["experiments_shared"], pair["experiments_in_either"]) == (
        len(shared),
        len(either),
    )
    assert pair["words_shared"] <= pair["words_in_either"]


def test_each_experiment_s_files_are_the_count_the_map_gives_it(served: DashboardServer) -> None:
    first, second = _two_sets_that_overlap(served)
    _, shown = _get(served, "groups/compare", [_spec(first), _spec(second)])
    _, drawn = _get(served, "themes/map")
    on_map = {point["id"]: point for point in drawn["experiments"]}
    seen = [e for group in shown["groups"] for e in group["experiments"]]
    assert seen
    for experiment in seen:
        assert experiment["id"] in on_map, experiment
        assert experiment["files"] == on_map[experiment["id"]]["files"]
        assert experiment["label"] == on_map[experiment["id"]]["folder"]


def test_a_line_of_work_is_compared_by_its_own_words_and_experiments(
    served: DashboardServer,
) -> None:
    status, shown = _get(served, "groups/compare", ["line:3", "line:1"])
    assert status == 200, shown
    _, themes = _get(served, "themes")
    by_theme = {item["theme"]: item for item in themes["themes"]}
    for group, number in zip(shown["groups"], (3, 1), strict=True):
        assert (group["kind"], group["state"]) == ("line", None)
        assert group["key"] == f"line:{number}"
        assert group["words"] == sorted(by_theme[number]["words"])
        assert group["label"] == by_theme[number]["label"]
        wanted = {row["id"] for row in themes["experiments"] if row["theme"] == number}
        assert {e["id"] for e in group["experiments"]} == wanted
        assert len(group["experiments"]) == by_theme[number]["experiments"]
    # Two lines of work share no experiment: a line is a partition.
    assert shown["experiments_in_all"] == []
    assert shown["experiments_in_either"] == sum(len(g["experiments"]) for g in shown["groups"])
    assert shown["files_in_all"] == 0


def test_a_set_of_words_and_a_line_of_work_are_compared_in_one_answer(
    served: DashboardServer,
) -> None:
    sets = _sets(served)
    some = next(found for found in sets.values() if len(found["experiments"]) >= 5)
    status, shown = _get(served, "groups/compare", [_spec(some), "line:0"])
    assert status == 200, shown
    assert [g["kind"] for g in shown["groups"]] == ["words", "line"]
    assert shown["groups"][0]["state"] == some["state"]
    assert shown["groups"][0]["label"] == ", ".join(some["terms"])


def test_four_groups_give_six_pairs_and_the_most_experiments_are_in_all_four(
    served: DashboardServer,
) -> None:
    specs = [f"line:{number}" for number in (0, 1, 2, 3)]
    status, shown = _get(served, "groups/compare", specs)
    assert status == 200, shown
    assert [g["tag"] for g in shown["groups"]] == ["A", "B", "C", "D"]
    assert len(shown["pairs"]) == 6
    assert [p["first"] for p in shown["pairs"]][:3] == ["line:0", "line:0", "line:0"]
    for pair in shown["pairs"]:
        assert pair["experiments_shared"] <= pair["experiments_in_either"]
        assert pair["files_shared"] <= pair["files_in_either"]


def test_the_words_of_a_group_are_asked_the_same_in_any_spelling_order(
    served: DashboardServer,
) -> None:
    first, second = _two_sets_that_overlap(served)
    status, forward = _get(served, "groups/compare", [_spec(first), _spec(second)])
    reversed_spec = "words:" + "|".join(reversed(first["terms"]))
    status2, again = _get(served, "groups/compare", [reversed_spec.upper(), _spec(second)])
    assert (status, status2) == (200, 200)
    assert again["groups"][0]["key"] == forward["groups"][0]["key"]
    assert again["groups"] == forward["groups"]


@pytest.mark.parametrize(
    ("groups", "status", "code"),
    [
        ([], 400, "bad_parameter"),
        (["line:0"], 400, "bad_parameter"),
        ([f"line:{n}" for n in range(5)], 400, "bad_parameter"),
        (["line:0", "line:0"], 400, "bad_parameter"),
        (["line:0", "nonsense"], 400, "bad_parameter"),
        (["line:0", "line:x"], 400, "bad_parameter"),
        (["line:0", "words:"], 400, "bad_parameter"),
        (["line:0", "line:9999"], 404, "group_not_found"),
        (["line:0", "words:no|such set"], 404, "group_not_found"),
    ],
)
def test_a_comparison_that_cannot_be_made_is_refused_in_words(
    served: DashboardServer, groups: list[str], status: int, code: str
) -> None:
    got, shown = _get(served, "groups/compare", groups)
    assert got == status, shown
    assert shown["error"]["code"] == code
    assert shown["error"]["parameter"] == "group"
    assert len(shown["error"]["message"]) > 20
