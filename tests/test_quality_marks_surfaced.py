"""A quality mark a name carries is shown where a person reviews, in plain words.

The vocabulary has a poor quality mark (``sick``, ``unwell``: ``mark: poor``) beside ``good``: a
mark on quality, never a doubt, and never a reason to leave data out. The entry's readings, as
``/api/v1/entry`` returns them, list each mark its name carries with what it means, and a search
card's chip for such a word says it is a mark. Read-only: nothing is ranked, withheld or raised for
it. Every name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ingence.api import v1
from ingence.core.project import Project
from ingence.dictionary.quality_marks import quality_marks
from ingence.dictionary.store import DictionaryStore
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


def test_the_vocabulary_says_which_quality_words_are_marks() -> None:
    marks = quality_marks(DictionaryStore.builtin())
    assert marks["looks_unwell"].mark == "poor"
    assert marks["good"].mark == "good"
    assert "never a doubt" in marks["looks_unwell"].said
    assert "nothing is left out" in marks["looks_unwell"].said
    assert "not" in marks["looks_unwell"].negated_by


def _project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    for folder in ("kelt sick run", "vorn not sick run", "zib run"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "trell_01.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    return project


def _entry(project: Project, name: str) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        (identifier,) = catalog.query(
            "SELECT entry_id FROM entries WHERE name = ?", [name]
        ).fetchone()
    context = v1.context_for(project.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "entry", {"id": [identifier]})
    assert int(status) == 200, payload
    return payload


def test_an_entrys_readings_list_the_marks_its_name_carries(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (mark,) = _entry(project, "kelt sick run")["marks"]
    assert mark["term"] == "looks_unwell"
    assert mark["mark"] == "poor"
    assert mark["spelled"] == "sick"
    assert mark["cancelled_by"] is None
    assert "never a doubt" in mark["said"]
    # A word the vocabulary names standing directly before it cancels it, and says so.
    (cancelled,) = _entry(project, "vorn not sick run")["marks"]
    assert cancelled["cancelled_by"] == "not"
    assert _entry(project, "zib run")["marks"] == []


def test_a_search_cards_chip_for_a_mark_says_it_is_a_mark(tmp_path: Path) -> None:
    from ingence.api.routes import search as search_routes

    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        found = search_routes.search(catalog, project.path, text="kelt")
    chips = [
        chip
        for result in found["results"]
        for chip in (result.get("tags") or {}).get("chips", [])
        if chip.get("term") == "looks_unwell"
    ]
    assert chips
    assert all(chip["mark"] == "poor" and "never a doubt" in chip["said"] for chip in chips)


def test_a_chip_for_a_mark_its_folders_name_cancels_says_so(tmp_path: Path) -> None:
    from ingence.api.routes import search as search_routes

    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        found = search_routes.search(catalog, project.path, text="vorn")
    (chip,) = [
        chip
        for result in found["results"]
        for chip in (result.get("tags") or {}).get("chips", [])
        if chip.get("term") == "looks_unwell"
    ]
    assert chip["where"] == "named"
    assert chip["cancelled_by"] == "not"


def test_a_search_filters_by_a_mark_a_name_carries_and_not_a_cancelled_one(tmp_path: Path) -> None:
    """``mark:poor`` finds the units whose names carry the poor mark. A person's choice to narrow,
    never a withholding. A mark its word cancels (``not sick``) is not found."""
    from ingence.api.routes import search as search_routes

    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        marked = search_routes.search(catalog, project.path, text="run mark:poor")
        good = search_routes.search(catalog, project.path, text="run mark:good")
    assert [row["path"] for row in marked["results"]] == ["kelt sick run"]
    assert good["results"] == []
    assert not marked["refused"]


def test_a_mark_no_word_declares_is_refused_and_nothing_crashes(tmp_path: Path) -> None:
    """``mark:bad`` names no mark the vocabulary declares: refused and shown back, as ``has:``
    refuses its unknowns. A pattern the database cannot read must not crash the search."""
    from ingence.api.routes import search as search_routes
    from ingence.dictionary.quality_marks import QualityMark, cancel_pattern

    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        found = search_routes.search(catalog, project.path, text="kelt mark:bad")
    assert any("mark:bad" in refused for refused in found["refused"])
    assert "kelt sick run" in [row["path"] for row in found["results"]]
    # A mark no word cancels has no pattern to read, so the filter reads none.
    plain = QualityMark(term="kelt", mark="poor", said="", negated_by=(), surfaces=("kelt",))
    assert cancel_pattern([plain]) is None


def test_no_good_is_cancelled_and_both_readers_of_negated_by_agree() -> None:
    """The shipped ``negated_by`` words are strings. YAML would load ``no`` in ``[not, no]`` as
    False, so "no good" would be read as marked good, and "false good" would be cancelled by the
    search pattern (which folds case) but not by ``cancelled_by``. The two readers agree."""
    import re

    from ingence.dictionary.quality_marks import cancel_pattern, cancelled_by

    good = quality_marks(DictionaryStore.builtin())["good"]
    assert "no" in good.negated_by
    pattern = cancel_pattern([good])
    assert pattern is not None
    for name, cancelled in (
        ("not_good_kelt", "not"),
        ("no_good_kelt", "no"),
        ("false_good_kelt", None),
        ("No_Good_kelt", "no"),
    ):
        assert cancelled_by(name, "good", good.negated_by) == cancelled, name
        assert bool(re.search(pattern, name.casefold())) is (cancelled is not None), name
