"""The Coverage view's two controls: define an unbound word, annotate a folder.

A word nobody contests raises no question, so the one route that writes has no question to write
against. The word itself is the target, and the next index or rebind binds what the person said.
A folder annotation is the event that an imported annotation becomes, written from any row
naming a folder.
"""

from __future__ import annotations

import json

import pytest

from ingence.api.routes.decide import AnswerRefusedError, annotate_folder, define_word
from ingence.core.ids import PROJECT_SCOPE, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import DictionaryOrigin, RecordEventKind
from ingence.dictionary.store import DictionaryStore
from ingence.record.log import read_events
from ingence.record.questions import term_target, terms_in_force
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

#: A word the shipped Dictionary places nowhere, asserted below rather than assumed.
UNBOUND = "zorbex"


@pytest.fixture
def project(tmp_path) -> Project:
    pool = tmp_path / "pool"
    (pool / "day_one").mkdir(parents=True)
    for index in (1, 2):
        (pool / "day_one" / f"plate_{UNBOUND}_{index}.csv").write_text("a,b\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return project


def _define(project: Project, **overrides):
    arguments = {
        "actor": "robin",
        "occurred_at": "2026-09-23T12:00:00Z",
        "word": UNBOUND,
        "answer": "zorbex_reagent",
        "theme_kind": "reagent",
    }
    arguments.update(overrides)
    with open_catalog(project.path) as catalog:
        return define_word(catalog, project.path, **arguments)


def _terms(project: Project, run_id: str) -> list[tuple[str, str | None]]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        return catalog.query(
            "SELECT DISTINCT c.normalized_value, b.term FROM dictionary_bindings b "
            "JOIN components c USING (component_id) WHERE c.normalized_value = ?",
            [UNBOUND],
        ).fetchall()


def test_a_word_defined_from_the_coverage_view_binds_on_the_next_rebind(project) -> None:
    assert DictionaryStore.builtin().look_up(UNBOUND, scope=PROJECT_SCOPE) is None
    assert _terms(project, "run_20260101T000001Z_111111111111") == [(UNBOUND, None)]

    event = _define(project)
    assert event.kind is RecordEventKind.DEFINE
    assert event.target == term_target(UNBOUND)
    assert json.loads(event.payload)["as_shown"] == {"files": 2}
    [answer] = terms_in_force(project.path)
    assert (answer.raised, answer.term, answer.theme_kind, answer.rule_id) == (
        UNBOUND,
        "zorbex_reagent",
        "reagent",
        "",
    )
    hit = DictionaryStore.for_project(project.path).look_up(UNBOUND, scope=PROJECT_SCOPE)
    assert hit is not None and hit.sense.origin is DictionaryOrigin.USER

    report = run_census(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", rebind=True
    )
    assert _terms(project, report.run_id) == [(UNBOUND, "zorbex_reagent")]


def test_naming_a_sense_the_dictionary_holds_is_an_assignment_on_its_own_axis(project) -> None:
    held = next(sense for sense in DictionaryStore.builtin().senses() if sense.theme_kind)
    with pytest.raises(AnswerRefusedError, match="already a sense"):
        _define(project, answer=held.term, theme_kind=f"not_{held.theme_kind}")
    event = _define(project, answer=held.term, theme_kind=held.theme_kind)
    assert event.kind is RecordEventKind.ASSIGN
    # And once answered, the word is placed: changing it needs a new decision.
    with pytest.raises(AnswerRefusedError, match="already places"):
        _define(project)


def test_a_word_is_refused_where_nobody_wrote_it_or_the_dictionary_places_it(project) -> None:
    with pytest.raises(AnswerRefusedError, match="no name in the current run"):
        _define(project, word="quillatrix")
    with pytest.raises(AnswerRefusedError, match="already places"):
        _define(project, word="plate")
    with pytest.raises(AnswerRefusedError, match="are all needed"):
        _define(project, theme_kind=" ")
    assert read_events(project.path) == ()


def test_a_folder_is_annotated_from_any_row_as_a_sheet_row_is(project) -> None:
    with open_catalog(project.path) as catalog:
        folder = catalog.query("SELECT entry_id FROM entries WHERE name = 'day_one'").fetchone()[0]
        event = annotate_folder(
            catalog,
            project.path,
            actor="robin",
            occurred_at="2026-09-23T12:00:00Z",
            entry_id=folder,
            note="the first day's plates",
        )
        with pytest.raises(AnswerRefusedError, match="not a folder"):
            annotate_folder(
                catalog, project.path, actor="robin", occurred_at="t", entry_id="x", note="n"
            )
        with pytest.raises(AnswerRefusedError, match="says something"):
            annotate_folder(catalog, project.path, actor="robin", occurred_at="t", entry_id=folder)
    assert event.kind is RecordEventKind.ANNOTATE
    assert event.target == f"folder:{folder}"
    assert event.scope.kind is ScopeKind.FOLDER
    assert json.loads(event.payload) == {
        "note": "the first day's plates",
        "path": "day_one",
        "source": "coverage view",
    }
