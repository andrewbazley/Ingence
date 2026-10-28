"""The Years view where themes cannot be drawn.

The behaviour: where the theme engine is unavailable, experiments are still dated, from their
names, by the same rule the themes use. What is missing is said in plain words with how to get it.
The engine is switched off for the test with the registry's own switch (a component disabled);
the words for a missing package are checked on the registry's message. Every word and person is
invented (``fixtures/theme_pool.py``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import UNDATED, build_theme_project
from ingence.api import v1
from ingence.core.registry import ComponentNotAvailableError
from ingence.theme_words import unavailable_in_words
from ingence.themes import THEME_ENGINES


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    project, plant = build_theme_project(tmp_path_factory.mktemp("years_without_themes"))
    return v1.context_for(project, settled=None, job_running=False), plant


def _get(context, path):
    return v1.dispatch(context, path, {})


def test_missing_packages_are_named_as_people_install_them() -> None:
    error = ComponentNotAvailableError(
        "theme_engine 'lsa_louvain' needs sklearn, scipy, which is not installed"
    )
    said = unavailable_in_words(error)
    assert "scikit-learn and scipy" in said
    assert "python -m pip install scikit-learn scipy" in said
    assert "themes support" in said
    for developer_word in ("sklearn", "lsa_louvain", "theme_engine"):
        assert developer_word not in said


def test_a_switched_off_engine_and_any_other_failure_are_said_plainly() -> None:
    off = ComponentNotAvailableError("theme_engine 'lsa_louvain' is registered but disabled")
    assert unavailable_in_words(off) == "themes are switched off in this installation"
    other = ComponentNotAvailableError("theme_engine 'lsa_louvain' could not be built: X: y")
    assert unavailable_in_words(other).startswith("themes could not start here")


def test_the_themes_route_says_what_is_missing_in_plain_words(served) -> None:
    context, _ = served
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        status, payload = _get(context, "themes")
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    assert status == 503
    assert payload["error"]["message"] == "themes are switched off in this installation"


def test_experiments_are_dated_from_their_names_without_themes(served) -> None:
    context, plant = served
    _, with_themes = _get(context, "themes")
    on_themes = {row["id"]: row["date"] for row in with_themes["experiments"]}
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        status, payload = _get(context, "completeness")
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    assert status == 200, payload
    assert payload["themes_computed"] is False
    assert "sklearn" not in payload["themes_said"]
    assert "switched off" in payload["themes_said"]
    dated = {row["id"]: row["date"] for row in payload["experiments"]}
    assert dated == on_themes
    undated = {plant.folders[u] for u in UNDATED}
    assert {row["folder"] for row in payload["experiments"] if row["date"] is None} == undated
    assert sum(date is not None for date in dated.values()) > 0
    assert all(row["theme"] is None for row in payload["experiments"])
