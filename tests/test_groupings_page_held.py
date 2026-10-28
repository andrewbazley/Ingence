"""The Groupings page costs one page, not the whole list.

The class: a page of ``/api/pages/classes`` does not build the page's whole list again at every
offset, which would make paging through a large project's groups cost the whole list each time. The
list is held per settled run and Record state: built once, then every page is cut from it; a new
answer or a new run builds it again. The rows a page shows are the rows the whole list gives. Every
name is invented.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes import pages
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

_RUN = "run_20260101T000002Z_222222222222"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("groupings-held")
    pool = base / "pool"
    for number, (first, second) in enumerate(
        (("kelt", "vorn"), ("zeph", "brisa"), ("quolt", "sorn"), ("trell", "dask"))
    ):
        # Undated folders holding no acquisition: no experiment holds their groups, so each is
        # asked on the page itself.
        folder = pool / f"{first}_batch{number}"
        folder.mkdir(parents=True)
        for condition in (first, second, "morv"):
            for index in (1, 2, 3):
                (folder / f"zib_{condition}_{index:02d}.csv").write_text(f"a\n{index}\n")
    made = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(made, run_id=_RUN, started_at="t")
    return made


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count how often the Groupings page's whole list is built."""
    built: list[int] = []
    whole = pages._class_rows

    def counted(*arguments: Any, **named: Any) -> Any:
        built.append(1)
        return whole(*arguments, **named)

    monkeypatch.setattr(pages, "_class_rows", counted)
    return built


def test_paging_builds_the_whole_list_once_and_shows_the_same_rows(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages.forget_held()
    with open_catalog(project.path) as catalog:
        whole = pages.page(catalog, project.path, "classes", limit=5000)
    assert whole["listed"] >= 4, "the invented pool forms groups to page through"
    built = _counting(monkeypatch)
    pages.forget_held()
    seen = []
    with open_catalog(project.path) as catalog:
        for offset in range(whole["listed"] + 1):
            one = pages.page(catalog, project.path, "classes", limit=1, offset=offset)
            assert one["listed"] == whole["listed"]
            assert one["progress"] == whole["progress"]
            seen.extend(one["rows"])
    assert built == [1]
    assert seen == whole["rows"]


def test_a_page_handed_to_a_caller_leaves_the_held_rows_as_they_were(project: Project) -> None:
    pages.forget_held()
    with open_catalog(project.path) as catalog:
        first = pages.page(catalog, project.path, "classes", limit=5000)
        first["rows"][0]["target"] = "changed by a caller"
        again = pages.page(catalog, project.path, "classes", limit=5000)
    assert again["rows"][0]["target"] != "changed by a caller"


def test_a_new_answer_is_read_onto_the_list_without_building_it_again(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _counting(monkeypatch)
    pages.forget_held()
    with open_catalog(project.path) as catalog:
        before = pages.page(catalog, project.path, "classes", limit=5000)
        pages.page(catalog, project.path, "classes", limit=1, offset=2)
        answered = before["rows"][0]
        append_event(
            project.path,
            kind=RecordEventKind.CONFIRM,
            actor="a person",
            occurred_at="2026-01-01T00:00:03Z",
            target=answered["target"],
            against_run_id=_RUN,
            payload={},
        )
        after = pages.page(catalog, project.path, "classes", limit=5000)
        pages.forget_held()
        whole = pages.page(catalog, project.path, "classes", limit=5000)
    assert built == [1, 1]  # once for the run, once more only after letting go of it
    assert after["progress"]["answered"] == before["progress"]["answered"] + 1
    assert answered["target"] not in {row["target"] for row in after["rows"]}
    assert after == whole  # the answers read onto the held rows, as a fresh build reads them


def test_a_new_run_builds_the_list_again(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    built = _counting(monkeypatch)
    pages.forget_held()
    with open_catalog(project.path) as catalog:
        pages.page(catalog, project.path, "classes", limit=1)
    run_inference(project, run_id="run_20260101T000005Z_555555555555", started_at="t")
    with open_catalog(project.path) as catalog:
        pages.page(catalog, project.path, "classes", limit=1)
        pages.page(catalog, project.path, "classes", limit=1, offset=1)
    assert built == [1, 1]


def test_a_late_page_of_a_long_list_costs_a_fraction_of_the_first(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twenty thousand groups: the page at offset 19,000 is the same rows, cut from the list."""
    groups = [
        {
            "target": f"class:zib_kelt_{number:05d}",
            "rule_id": "replicates_of_one_condition",
            "folders": ["kelt_batch0"],
            "members": 3,
        }
        for number in range(20_000)
    ]

    def slow(*arguments: Any, **named: Any) -> Any:
        del arguments, named
        time.sleep(1.0)  # what building the whole list costs, standing in for a large project
        return [dict(group) for group in groups], {}

    monkeypatch.setattr(pages, "_class_rows", slow)
    pages.forget_held()
    with open_catalog(project.path) as catalog:
        started = time.perf_counter()
        first = pages.page(catalog, project.path, "classes", limit=50)
        first_took = time.perf_counter() - started
        started = time.perf_counter()
        late = pages.page(catalog, project.path, "classes", limit=50, offset=19_000)
        late_took = time.perf_counter() - started
    assert [row["target"] for row in first["rows"]] == [g["target"] for g in groups[:50]]
    assert [row["target"] for row in late["rows"]] == [g["target"] for g in groups[19_000:19_050]]
    assert late_took < first_took / 10


def test_the_tabs_counts_and_the_page_share_one_build_and_an_answer_rebuilds_nothing(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tabs' counts read every page; the Groupings page's part of them is the held list's."""
    pages.forget_held()
    with open_catalog(project.path) as catalog:
        fresh = pages.waiting(catalog, project.path)
        pages.forget_held()
        page_alone = pages.page(catalog, project.path, "classes", limit=5000)
    built = _counting(monkeypatch)
    pages.forget_held()
    with open_catalog(project.path) as catalog:
        counted = pages.waiting(catalog, project.path)
        shown = pages.page(catalog, project.path, "classes", limit=5000)
        assert counted == fresh
        assert counted["classes"] == shown["progress"]["remaining"]
        # The page shown after the counts writes each set's names as a page built alone does.
        assert shown == page_alone
        assert all(
            row.get("pattern") is not None for row in shown["rows"] if row["level"] == "files"
        )
        # A set one rule formed alone, so one answer answers it.
        waiting_row = next(row for row in shown["rows"] if not row.get("also"))
        append_event(
            project.path,
            kind=RecordEventKind.CONFIRM,
            actor="a person",
            occurred_at="2026-01-01T00:00:04Z",
            target=waiting_row["target"],
            against_run_id=catalog.run_directory.name if catalog.run_directory else _RUN,
            payload={},
        )
        again = pages.waiting(catalog, project.path)
    assert built == [1]
    assert again["classes"] == counted["classes"] - 1


def test_one_list_serves_every_order_and_few_projects_are_held(
    project: Project, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The held list is not kept once per order asked, since the Groupings page reads no order,
    and it is let go for a project no longer served. At most ``pages.HELD_PROJECTS`` projects are
    held."""
    built = _counting(monkeypatch)
    pages.forget_held()
    with open_catalog(project.path) as catalog:
        plain = pages.page(catalog, project.path, "classes", limit=5000)
        ordered = pages.page(catalog, project.path, "classes", limit=5000, order=["findings"])
    assert built == [1]
    assert ordered["rows"] == plain["rows"]
    pages.forget_held(project.path)
    assert not any(key[0] == str(project.path.resolve()) for key in pages._HELD)
    for number in range(pages.HELD_PROJECTS + 1):
        pages._HELD[(str(tmp_path / f"other{number}"), "classes")] = ("r", (0, 0), [], {})
        pages._hold((str(tmp_path / f"other{number}"), "classes"))
    assert len({key[0] for key in pages._HELD}) == pages.HELD_PROJECTS
    pages.forget_held()
