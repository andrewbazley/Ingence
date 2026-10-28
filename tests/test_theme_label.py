"""A person's name for a line of work: the ``theme:`` target and the themes route.

One ``annotate`` event on ``theme:<key>`` through the one answer route. The themes route serves the
name in force beside the engine's own label, and a later annotation with no name removes it. The
test uses the invented theme pool (``fixtures.theme_pool``): no laboratory name anywhere.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from ingence.api.v1 import context_for, dispatch
from ingence.record.targets import theme_key, theme_named_by, theme_target

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip("sklearn", reason="themes need the themes extra")


@pytest.fixture(scope="module")
def theme_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from fixtures.theme_pool import build_theme_project

    project, _ = build_theme_project(tmp_path_factory.mktemp("themes"))
    return project


def test_theme_key_is_the_words_sorted() -> None:
    assert theme_key(["TrackMate", "quolt", "tamen"]) == "quolt+tamen+trackmate"
    assert theme_key(["b", " a ", ""]) == "a+b"
    assert theme_named_by(theme_target("a+b")) == "a+b"
    assert theme_named_by("folder:entry_1") is None


def test_themes_route_serves_a_target_and_no_name(theme_project: Path) -> None:
    context = context_for(theme_project, settled=None, job_running=False)
    status, body = dispatch(context, "themes", {})
    assert status == 200
    first = body["themes"][0]
    assert first["target"] == theme_target(theme_key(first["label"].split(" · ")))
    assert first["person_label"] is None
    assert first["labelled_by"] is None


def test_a_name_is_written_served_and_removed(theme_project: Path, tmp_path: Path) -> None:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.storage.catalog import open_catalog

    project = tmp_path / "project"
    shutil.copytree(theme_project, project)
    context = context_for(project, settled=None, job_running=False)
    _, body = dispatch(context, "themes", {})
    first = body["themes"][0]
    with open_catalog(project) as catalog:
        event = decide_target(
            DecisionContext(catalog=catalog, project=project),
            Decision.from_body(
                {
                    "target": first["target"],
                    "described": "A line a person named",
                    "reading": first["label"],
                }
            ),
            actor="a. person",
            occurred_at="2026-10-09T05:00:00Z",
        )
    assert event.kind.value == "annotate"
    assert event.target == first["target"]
    _, body = dispatch(context, "themes", {})
    named = next(t for t in body["themes"] if t["target"] == first["target"])
    assert named["person_label"] == "A line a person named"
    assert named["labelled_by"] == "a. person"
    assert named["label"] == first["label"], "the engine's own words stay"
    # A note alone removes the name from view; the earlier event stays in the Record.
    with open_catalog(project) as catalog:
        decide_target(
            DecisionContext(catalog=catalog, project=project),
            Decision.from_body({"target": first["target"], "note": "the name was removed"}),
            actor="a. person",
            occurred_at="2026-10-09T05:01:00Z",
        )
    _, body = dispatch(context, "themes", {})
    named = next(t for t in body["themes"] if t["target"] == first["target"])
    assert named["person_label"] is None
    assert named["person_note"] == "the name was removed"


def test_an_empty_name_is_refused(theme_project: Path, tmp_path: Path) -> None:
    from ingence.api.routes.decide import (
        AnswerRefusedError,
        Decision,
        DecisionContext,
        decide_target,
    )
    from ingence.storage.catalog import open_catalog

    project = tmp_path / "project"
    shutil.copytree(theme_project, project)
    with open_catalog(project) as catalog, pytest.raises(AnswerRefusedError):
        decide_target(
            DecisionContext(catalog=catalog, project=project),
            Decision.from_body({"target": "theme:a+b", "described": "   "}),
            actor="a. person",
            occurred_at="2026-10-09T05:00:00Z",
        )
