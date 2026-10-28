"""``/api/v1/themes...``: the run's themes served as declared models.

Every themes route answers its declared model from the run reads are pinned to, in the Dashboard
design's names. It never writes anything, keeps what cannot be placed to a short page, and says in
the one error shape when there is no run or no themes component. Every word and person is invented
(``fixtures/theme_pool.py``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import OUTLIERS, PLANT, UNDATED, build_theme_project
from ingence.api import v1
from ingence.api.v1.models import RESPONSE_MODELS, ErrorResponse
from ingence.core.project import Project
from ingence.themes import THEME_ENGINES

ROUTES = (
    "themes",
    "themes/stability",
    "themes/years",
    "themes/who_else",
    "themes/undated",
    "themes/unplaced",
)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    project, plant = build_theme_project(tmp_path_factory.mktemp("themes_api"))
    return v1.context_for(project, settled=None, job_running=False), plant


def _get(context, path, **query):
    status, payload = v1.dispatch(context, path, {k: [str(v)] for k, v in query.items()})
    return status, payload


@pytest.mark.parametrize("path", ROUTES)
def test_each_route_answers_its_registered_model(served, path) -> None:
    context, _ = served
    status, payload = _get(context, path)
    assert status == 200, payload
    v1.DECLARED[path].model_validate(payload)
    assert v1.DECLARED[path] in set(RESPONSE_MODELS.values())
    assert payload["run_id"] == context.run_pinned


def test_themes_carry_the_design_s_names(served) -> None:
    context, plant = served
    _, payload = _get(context, "themes")
    row = payload["experiments"][0]
    for name in (
        "id",
        "folder",
        "date",
        "last_file_time",
        "year",
        "files",
        "bytes",
        "theme",
        "theme_label",
        "broad_theme",
        "broad_label",
        "words",
    ):
        assert name in row
    assert payload["summary"]["themes"] == len(PLANT)
    assert payload["summary"]["experiments"] == len(plant.documents)
    assert payload["unplaced_note"].endswith("no theme")


def test_the_unplaced_page_is_short_and_says_why(served) -> None:
    context, plant = served
    _, payload = _get(context, "themes/unplaced", limit=2)
    assert len(payload["shown"]) == 2
    assert payload["not_shown"] == payload["count"] - 2
    assert payload["note"] == f"{payload['count']} experiments have no theme"
    assert all(item["reason"] for item in payload["shown"])
    _, every = _get(context, "themes/unplaced", limit=100)
    folders = {item["folder"] for item in every["shown"]}
    assert {plant.folders[u] for u in OUTLIERS} <= folders


def test_stability_per_theme(served) -> None:
    context, _ = served
    _, payload = _get(context, "themes/stability")
    assert payload["seed_ami"] is not None and payload["seed_nmi"] is not None
    assert len(payload["themes"]) == len(PLANT)
    assert all(0 <= t["stability"] <= 1 for t in payload["themes"])
    assert payload["sweep"]


def test_years_and_undated(served) -> None:
    context, plant = served
    _, years = _get(context, "themes/years")
    assert {t["first"] for t in years["themes"]} == {span[0] for _, span, _ in PLANT.values()}
    assert "2019" in years["story"]
    _, undated = _get(context, "themes/undated")
    assert {e["folder"] for e in undated["experiments"]} == {plant.folders[u] for u in UNDATED}


def test_who_wrote_a_word(served) -> None:
    context, _ = served
    _, payload = _get(context, "themes/who_else", word="hoben")
    assert payload["person_level"] == 1
    assert payload["person_rule_is_default"] is True
    assert {m["person"] for m in payload["matches"]} == set(PLANT["t4"][2])
    # A prefix of nobody's word inside another word does not match (never a substring search).
    _, none = _get(context, "themes/who_else", word="obe")
    assert none["matches"] == []


def test_no_run_and_no_component_are_refused_in_the_one_shape(served, tmp_path) -> None:
    context, _ = served
    empty = Project.create(tmp_path / "empty", created_at="t", roots=[tmp_path])
    status, payload = _get(v1.context_for(empty.path, settled=None, job_running=False), "themes")
    assert status == 404
    ErrorResponse.model_validate(payload)
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        status, payload = _get(context, "themes/years")
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    assert status == 503
    assert ErrorResponse.model_validate(payload).error.code == "component_unavailable"
