"""Search's date filter: the files' earliest credible times, a name's date only a neighbourhood.

A date written in a name is a neighbourhood, never the file's clock. So ``after:`` and ``before:``
match a unit by its files' earliest credible times. A unit whose name's date alone falls in the
range is still found and labelled as matched by its name's date, which widens the range and never
stands for the clock. Where no rule reads time, a file's own time is its content's or its
modification's, never the date its name spells. Every name here is invented.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from fixtures.revision_4 import write_revision
from ingence.api.routes import search as routes
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


def _run(tail: str) -> str:
    return f"run_{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}Z_{tail * 12}"


def _pool(base: Path) -> Path:
    pool = base / "pool"
    # A folder named for a day in 2019 whose files were written today.
    (pool / "20190412 kelt droplet").mkdir(parents=True)
    for name in ("kelt_vorn_001.txt", "kelt_vorn_002.txt"):
        (pool / "20190412 kelt droplet" / name).write_text("x")
    # A file whose own name spells a day in 2019, in an undated folder, written today.
    (pool / "kelt brisa").mkdir()
    (pool / "kelt brisa" / "20190412_kelt_brisa.txt").write_text("x")
    return pool


def _search(project: Project, text: str) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return routes.search(catalog, project.path, text=text)


def _by_path(found: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["path"]): row for row in found["results"]}


def test_a_units_name_date_widens_the_range_as_a_labelled_neighbourhood(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    write_revision(project.path)
    run_census(project, run_id=_run("1"), started_at="t")
    run_inference(project, run_id=_run("2"), started_at="t")
    # Its files were written this year: their earliest credible times match "after 2025".
    recent = _by_path(_search(project, "kelt droplet after:2025"))
    card = recent["20190412 kelt droplet"]
    assert card["tags"]["date_match"]["by"] == "earliest_credible_time"
    # Nothing it holds is credibly before 2020, but its name's date is: found, and said to be.
    early = _by_path(_search(project, "kelt droplet before:2020"))
    card = early["20190412 kelt droplet"]
    assert card["tags"]["date_match"]["by"] == "name_date"
    assert "never" in card["tags"]["date_match"]["said"]
    # A range neither its files nor its name reach finds nothing.
    assert _search(project, "kelt droplet before:2018")["results"] == []


def test_without_a_time_rule_a_name_date_is_never_the_files_time(tmp_path: Path) -> None:
    """Without a time rule: the book at revision 3."""
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.resolve import effective_rulebook

    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    run_census(project, run_id=_run("1"), started_at="t")
    run_inference(
        project,
        run_id=_run("2"),
        started_at="t",
        rulebook=effective_rulebook(load_revisions(), at=3),
    )
    with open_catalog(project.path) as catalog:
        (source,) = catalog.query(
            "SELECT event_time_source FROM entries WHERE name = '20190412_kelt_brisa.txt'"
        ).fetchone()
    assert source == "filename"  # the census reads the name's date as its name's clock
    # That clock is not read as the file's time, so "before 2020" does not find it.
    assert "kelt brisa" not in _by_path(_search(project, "kelt brisa before:2020"))
    assert "kelt brisa" in _by_path(_search(project, "kelt brisa after:2025"))


def test_a_negated_date_filter_is_never_hidden_by_a_name_date(tmp_path: Path) -> None:
    """``-before:2020`` keeps a unit none of whose files is credibly before 2020, whatever its
    name's date says.

    A neighbourhood only ever widens a range, and never hides a unit.
    """
    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    write_revision(project.path)
    run_census(project, run_id=_run("1"), started_at="t")
    run_inference(project, run_id=_run("2"), started_at="t")
    card = _by_path(_search(project, "kelt droplet -before:2020"))["20190412 kelt droplet"]
    said = card["tags"]["date_match"]["said"]
    assert "none of its files" in said
    assert "fall in the range" not in said
    assert _search(project, "kelt droplet -after:2025")["results"] == []


def test_a_typed_day_finds_a_swap_spelling_as_one_of_its_two_readings(tmp_path: Path) -> None:
    """A day and a month that could swap name both readings.

    A day typed finds a spelling holding it either way round, and the card says it is one of two
    readings.
    """
    pool = tmp_path / "pool"
    for folder in ("zib 05.06.2022 kelt", "zib 06.05.2022 kelt", "zib 2022-06-05 kelt"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "vorn_001.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=_run("1"), started_at="t")
    found = _by_path(_search(project, "2022-06-05"))
    assert set(found) == {"zib 05.06.2022 kelt", "zib 06.05.2022 kelt", "zib 2022-06-05 kelt"}
    for path in ("zib 05.06.2022 kelt", "zib 06.05.2022 kelt"):
        assert any(line.get("one_of_two_readings") for line in found[path]["why"])
    assert not any(line.get("one_of_two_readings") for line in found["zib 2022-06-05 kelt"]["why"])
    # The other reading finds the two swaps and not the other day written once.
    assert set(_by_path(_search(project, "2022-05-06"))) == {
        "zib 05.06.2022 kelt",
        "zib 06.05.2022 kelt",
    }
    # A swap typed is read both ways too.
    assert set(_by_path(_search(project, "06.05.2022"))) == set(found)
