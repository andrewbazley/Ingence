"""A core is followed through the lattice's one mapping from a word to the key it uses.

``groups.words_as`` says whether a word the Dictionary binds is its term or its spelling in the
groups. A topic's core is read through the same mapping (``grouping/lattice_words.WordMap``), so a
core followed by the words a person once chose finds its experiments under either reading, and the
topic does not lose a core when the setting is switched. The test uses the themes' invented pool, in
which one invented word is written as a shipped Dictionary spelling
(``fixtures.theme_pool.ENGINE_SURFACES``).
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes.overlay import prepare_overlay, write_prepared
from ingence.api.v1 import context_for, dispatch

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.topic_project import TopicProject

pytest.importorskip("sklearn", reason="the fixture project draws themes")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> TopicProject:
    from fixtures.topic_project import build_topic_project

    return build_topic_project(tmp_path_factory.mktemp("topics_words"))


def _carrying(built: TopicProject, *words: str) -> set[str]:
    return {
        built.ids[unit]
        for unit, written in built.plant.documents.items()
        if unit in built.ids and set(words) <= set(written)
    }


def _core_experiments(project: Path, terms: list[str]) -> tuple[set[str], str]:
    prepared = prepare_overlay(
        project,
        actor="a. person",
        occurred_at="2026-06-01T10:00:00Z",
        edits=[{"kind": "topic", "name": "Probe", "members": [{"kind": "core", "terms": terms}]}],
    )
    write_prepared(project, prepared, actor="a. person", occurred_at="2026-06-01T10:00:00Z")
    context = context_for(project, settled=None, job_running=False)
    status, body = dispatch(context, "topics", {"suggestions": ["0"]})
    assert status == 200, body
    [member] = body["topics"][0]["members"]
    return set(member["experiment_ids"]), member["state"]


@pytest.mark.parametrize("words_as", ["terms", "spellings"])
@pytest.mark.parametrize("other", ["nuntle", "gavish"])
def test_a_core_finds_its_experiments_under_either_reading_of_a_bound_word(
    built: TopicProject, tmp_path: Path, words_as: str, other: str
) -> None:
    """The invented word ``ostrel`` is written as a Dictionary spelling whose term is
    ``epifluorescence``: a core followed by the term finds the experiments carrying it with another
    word, whether the groups read terms or spellings."""
    from ingence.core.project import Project
    from ingence.core.settings import GroupSettings

    copy = tmp_path / "project"
    shutil.copytree(built.project, copy)
    opened = Project.open(copy)
    opened = opened.save_settings(
        opened.settings.model_copy(update={"groups": GroupSettings(words_as=words_as)})
    )
    # The reading is the run's own (the setting it was bound by, from its journal): a change of the
    # setting waits for the next run.
    from ingence.grouping.lattice_words import run_words_as
    from ingence.infer.run import run_inference
    from ingence.storage.catalog import open_catalog

    run_inference(opened, run_id="run_20260101T000007Z_ffffffffffff", started_at="t")
    with open_catalog(copy) as catalog:
        assert run_words_as(catalog) == words_as
    expected = _carrying(built, "ostrel", other)
    assert expected
    found, state = _core_experiments(copy, ["epifluorescence", other])
    assert state == "resolved"
    assert found == expected


def test_the_key_of_a_word_has_one_home(built: TopicProject) -> None:
    """The core is read through the lattice's mapping where the run was read, and by the one
    fallback function only for a view built by hand."""
    from ingence.topics.runview import load_view

    view = load_view(built.project, built.infer, cores=True)
    assert view.core_terms and view.core_ways is not None
    assert view.core_ways(["Nuntle"]) == [("nuntle",)]
    plain: Any = load_view(built.project, built.infer)
    assert plain.core_ways is None and plain.core_terms is None


def test_only_the_errors_a_page_can_meet_are_told_as_could_not_be_read(
    built: TopicProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A defect in the lattice's helpers (a name changed, a wrong type) is not hidden as "the
    words could not be read"; a rule the lattice cannot read is said in words."""
    import ingence.api.v1.groups as groups
    from ingence.rulebook.registry import PredicateNotEvaluableError
    from ingence.topics.runview import read_cores, run_base

    base = run_base(built.project, built.infer)

    def defect(*_args: Any, **_kwargs: Any) -> Any:
        message = "a defect, not a reading that failed"
        raise RuntimeError(message)

    def unreadable(*_args: Any, **_kwargs: Any) -> Any:
        message = "the rule cannot be read"
        raise PredicateNotEvaluableError(message)

    monkeypatch.setattr(groups, "_term_sets", defect)
    with pytest.raises(RuntimeError, match="a defect"):
        read_cores(built.project, built.infer, base)
    monkeypatch.setattr(groups, "_term_sets", unreadable)
    terms, ways, why = read_cores(built.project, built.infer, base)
    assert terms is None and ways is None
    assert why == "the words experiments carry could not be read: PredicateNotEvaluableError"


@pytest.mark.parametrize("words_as", ["terms", "spellings"])
def test_a_core_with_a_two_word_term_is_written_and_resolves(
    built: TopicProject, tmp_path: Path, words_as: str
) -> None:
    """The invented word ``ostrel`` is written as the Dictionary spelling "widefield", which the
    names carry as the two words "wide field": a core followed by that phrase is written, and finds
    the experiments carrying it under either reading of a bound word (under terms it is the term
    the phrase is bound to)."""
    from ingence.core.project import Project
    from ingence.core.settings import GroupSettings
    from ingence.grouping.lattice_words import run_words_as
    from ingence.infer.run import run_inference
    from ingence.storage.catalog import open_catalog

    copy = tmp_path / "project"
    shutil.copytree(built.project, copy)
    opened = Project.open(copy)
    opened = opened.save_settings(
        opened.settings.model_copy(update={"groups": GroupSettings(words_as=words_as)})
    )
    run_inference(opened, run_id="run_20260101T000007Z_ffffffffffff", started_at="t")
    with open_catalog(copy) as catalog:
        assert run_words_as(catalog) == words_as
    found, state = _core_experiments(copy, ["wide field", "nuntle"])
    assert state == "resolved"
    assert found == _carrying(built, "ostrel", "nuntle")
