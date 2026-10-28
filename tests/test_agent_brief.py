"""``ingence export brief``: two files and a key, within a budget.

Every name here is invented. The sample collection is the shipped one.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fixtures.agent_sample import (
    SampleProject,
    build_sample_project,
    checkable_names,
    names_of_the_pool,
    survivors,
)
from ingence.agent.brief import (
    BRIEF_FILES,
    CHARACTERS_PER_TOKEN,
    DEFAULT_BUDGET,
    SENTENCE,
    build_brief,
    write_brief,
)
from ingence.agent.shape import LEVELS, Shaper, Vocabulary
from ingence.api.v1 import examples
from ingence.cli.main import app
from ingence.core.project import Project

_RUN = "run_20260101T000002Z_222222222222"
_HEADINGS = (
    "## Project counts",
    "## Areas",
    "## Experiments",
    "## Themes",
    "## Topics and goals",
    "## Assumptions",
    "## Open questions",
    "## What was not read",
    "## Changed since the last run",
)


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> SampleProject:
    return build_sample_project(tmp_path_factory.mktemp("agent_brief"))


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(app, ["export", "brief", *args])


# -- the brief's shape -----------------------------------------------------------------------------


def test_the_brief_opens_with_what_it_carries_and_the_one_sentence_once(
    sample: SampleProject,
) -> None:
    brief = build_brief(sample.project, level="words")
    lines = brief.markdown.splitlines()
    assert lines[0].startswith("# Ingence brief at level words: numbers, the Dictionary's words")
    assert lines[1] == SENTENCE
    assert brief.markdown.count(SENTENCE) == 1
    assert SENTENCE not in brief.json_text
    headings = [line for line in lines if line.startswith("## ")]
    assert headings == list(_HEADINGS)
    assert brief.data["level"] == "words" and brief.data["budget_tokens"] == DEFAULT_BUDGET
    assert set(brief.data["sections"]) >= {"overview", "areas", "experiments", "themes"}


@pytest.mark.parametrize("level", LEVELS)
def test_every_level_fits_the_budget_and_counts_fits_four_thousand_tokens_with_room(
    sample: SampleProject, level: str
) -> None:
    brief = build_brief(sample.project, level=level)  # type: ignore[arg-type]
    assert len(brief.markdown) <= DEFAULT_BUDGET * CHARACTERS_PER_TOKEN
    assert brief.replaced == 0
    if level == "counts":
        # Numbers only: with room, so a larger project's counts still fit.
        assert len(brief.markdown) // CHARACTERS_PER_TOKEN < DEFAULT_BUDGET // 2
        assert not re.search(r"\bE\d+\b", brief.markdown)
    else:
        assert re.search(r"^- E1\b", brief.markdown, re.MULTILINE)


def test_the_experiments_are_cut_to_the_budget_and_the_data_says_the_same(
    sample: SampleProject,
) -> None:
    brief = build_brief(sample.project, level="words", budget_tokens=1200)
    assert len(brief.markdown) <= 1200 * CHARACTERS_PER_TOKEN
    listed = re.findall(r"^- E\d+ \(", brief.markdown, re.MULTILINE)
    assert listed
    cut = re.search(
        r"^- \.\.\. and (\d+) more experiments \(over the budget\)\.$", brief.markdown, re.M
    )
    assert cut is not None
    section = brief.data["sections"]["experiments"]
    assert len(section["rows"]) == len(listed)
    assert section["not_listed"] == int(cut.group(1))
    # Every other section keeps its heading and its count line, however small the budget.
    for heading in _HEADINGS:
        assert heading in brief.markdown


def test_the_alias_key_is_beside_the_brief_and_never_inside_it(
    sample: SampleProject, tmp_path: Path
) -> None:
    brief = build_brief(sample.project, level="words")
    written = write_brief(brief, tmp_path / "out")
    assert [path.name for path in written] == list(BRIEF_FILES)
    key = json.loads(written[2].read_text())
    assert key["experiment"]["E1"].startswith("entry_")
    for identifier in key["experiment"].values():
        assert identifier not in brief.markdown and identifier not in brief.json_text


@pytest.mark.parametrize("level", ["counts", "words"])
def test_no_name_of_the_pool_reaches_the_brief_at_counts_or_words(
    sample: SampleProject, level: str
) -> None:
    names, people = names_of_the_pool(sample)
    checked = checkable_names(names, Vocabulary.for_project(sample.project.path))
    checked |= checkable_names(people, Vocabulary())
    # The brief's own words (its opening says "file contents") are not names of the data.
    own = build_brief(sample.project, level=level, fetch=_refusing)  # type: ignore[arg-type]
    own_words = {word.casefold() for word in re.findall(r"[^\W_]+", own.markdown)}
    checked = {name for name in checked if name.casefold() not in own_words}
    brief = build_brief(sample.project, level=level)  # type: ignore[arg-type]
    assert survivors(brief.markdown, checked) == []
    assert survivors(brief.json_text, checked) == []
    assert str(sample.pool) not in brief.markdown + brief.json_text


# -- a million files -------------------------------------------------------------------------------


def _refusing(route: str, query: Any) -> tuple[int, dict[str, Any]]:
    del route, query
    return 409, {"api_version": "1.0", "error": {"code": "no_published_run", "message": "none"}}


def _large(files: int, experiments: int) -> Any:
    """A reader answering a synthetic overview of a large collection, and its themes."""
    overview = examples.overview_example(_RUN)
    overview = overview.model_copy(
        update={
            "counts": overview.counts.model_copy(
                update={"files": files, "experiments": experiments}
            )
        }
    )
    themes = {
        "summary": {"themes": 2, "placed": experiments - 10, "unplaced": 10, "undated": 0},
        "themes": [
            {
                "theme": 0,
                "label": "timecourse imaging",
                "experiments": experiments // 2,
                "first": 2023,
                "last": 2024,
                "stability": 0.9,
                "direction": "rising",
                "by_year": {"2023": experiments // 4, "2024": experiments // 4},
            },
            {
                "theme": 1,
                "label": "titration plate",
                "experiments": experiments // 2,
                "first": 2022,
                "last": 2023,
                "stability": 0.8,
                "direction": "steady",
                "by_year": {"2022": experiments // 4, "2023": experiments // 4},
            },
        ],
    }

    def fetch(route: str, query: Any) -> tuple[int, dict[str, Any]]:
        if route == "overview":
            return 200, overview.model_dump(mode="json")
        if route == "themes":
            return 200, themes
        return _refusing(route, query)

    return fetch


def test_at_a_million_files_the_experiments_are_grouped_and_the_brief_does_not_grow(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    shaper = Shaper("words", vocabulary=Vocabulary.shipped())
    million = build_brief(project, level="words", fetch=_large(1_000_000, 50_000), shaper=shaper)
    assert "## Experiments (grouped)" in million.markdown
    assert "- Experiments: 50,000 in 1,000,000 files: too many to list" in million.markdown
    assert "- by area: A1 (instrument): 14 experiments, 1,240 files" in million.markdown
    assert "- by theme: theme 0 timecourse imaging: 25,000 experiments" in million.markdown
    assert "- by year: 2023: 25,000 experiments" in million.markdown
    assert not re.search(r"^- E\d+ \(", million.markdown, re.MULTILINE)
    assert million.data["sections"]["experiments"]["grouped"] is True
    assert million.data["sections"]["experiments"]["by_year"] == {
        "2022": 12_500,
        "2023": 25_000,
        "2024": 12_500,
    }
    larger = build_brief(
        project,
        level="words",
        fetch=_large(10_000_000, 500_000),
        shaper=Shaper("words", vocabulary=Vocabulary.shipped()),
    )
    assert abs(len(larger.markdown) - len(million.markdown)) < 100
    assert len(million.markdown) <= DEFAULT_BUDGET * CHARACTERS_PER_TOKEN


def test_a_route_that_refuses_is_said_in_its_section_and_the_rest_goes_on(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    brief = build_brief(
        project, level="counts", fetch=_refusing, shaper=Shaper("counts", vocabulary=Vocabulary())
    )
    assert "- Overview: none." in brief.markdown
    assert "- Experiments: none." in brief.markdown
    assert brief.data["sections"]["overview"] == {"error": "none"}
    for heading in _HEADINGS:
        assert heading in brief.markdown


def test_a_reader_that_raises_with_a_path_is_said_by_its_type_only_below_names(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    home = Path.home() / "invented" / "missing.parquet"

    def raising(route: str, query: Any) -> tuple[int, dict[str, Any]]:
        raise FileNotFoundError(f"[Errno 2] No such file or directory: '{home}'")

    for level in ("counts", "words"):
        brief = build_brief(
            project,
            level=level,  # type: ignore[arg-type]
            fetch=raising,
            shaper=Shaper(level, vocabulary=Vocabulary()),  # type: ignore[arg-type]
        )
        assert "- Overview: FileNotFoundError." in brief.markdown
        for text in (brief.markdown, brief.json_text):
            assert str(Path.home()) not in text and "invented" not in text


def test_a_budget_too_small_or_a_level_unknown_is_refused_in_words(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    with pytest.raises(ValueError, match="at least 100"):
        build_brief(project, level="counts", budget_tokens=50, fetch=_refusing)
    with pytest.raises(ValueError, match="no level 'all'"):
        build_brief(project, level="all", fetch=_refusing)  # type: ignore[arg-type]


# -- the command -----------------------------------------------------------------------------------


def test_the_command_writes_the_three_files_into_the_projects_exports(
    sample: SampleProject,
) -> None:
    result = _invoke("--project", str(sample.project.path), "--level", "counts")
    assert result.exit_code == 0, result.output
    assert "brief at level counts: about " in result.output
    folder = sample.project.path / "projections" / "exports" / "brief"
    runs = list(folder.iterdir())
    assert len(runs) == 1
    assert sorted(path.name for path in runs[0].iterdir()) == sorted(BRIEF_FILES)
    assert (runs[0] / "brief.md").read_text().splitlines()[1] == SENTENCE


def _walk(directory: Path) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    for root, _, files in os.walk(directory):
        for name in files:
            path = Path(root) / name
            found[str(path.relative_to(directory))] = (path.stat().st_size, path.stat().st_mtime_ns)
    return found


def test_the_command_prints_the_brief_with_a_dash_and_writes_nothing_under_the_project(
    sample: SampleProject, tmp_path: Path
) -> None:
    before = _walk(sample.project.path)
    result = _invoke("--project", str(sample.project.path), "--out", "-", "--budget", "1200")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# Ingence brief at level words")
    assert len(result.output) <= 1200 * CHARACTERS_PER_TOKEN
    assert not (tmp_path / "brief.md").exists()
    # The themes projection the brief read was built in memory: no cache file was written.
    assert _walk(sample.project.path) == before


def test_the_command_refuses_a_folder_in_a_root_an_existing_file_and_a_wrong_level(
    sample: SampleProject, tmp_path: Path
) -> None:
    inside = _invoke("--project", str(sample.project.path), "--out", str(sample.pool / "brief"))
    assert inside.exit_code == 2 and "refused" in inside.output
    assert not (sample.pool / "brief").exists()
    out = tmp_path / "out"
    first = _invoke("--project", str(sample.project.path), "--out", str(out))
    assert first.exit_code == 0, first.output
    again = _invoke("--project", str(sample.project.path), "--out", str(out))
    assert again.exit_code == 2 and "refused" in again.output
    replaced = _invoke("--project", str(sample.project.path), "--out", str(out), "--overwrite")
    assert replaced.exit_code == 0, replaced.output
    level = _invoke("--project", str(sample.project.path), "--level", "everything")
    assert level.exit_code == 2 and "no level 'everything'" in level.output
    budget = _invoke("--project", str(sample.project.path), "--budget", "9")
    assert budget.exit_code == 2 and "at least 100" in budget.output


def test_a_project_without_a_run_is_refused(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[])
    result = _invoke("--project", str(project.path))
    # The strings job: the refusal says the project has no scan yet.
    assert result.exit_code == 1 and "has no scan yet" in result.output
