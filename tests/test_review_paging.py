"""Every row of a review page counted, whatever a page shows.

The engine puts no cap on what it proposes, and the Dashboard shows 50 at a time. The page route
counts every row the filters keep (``listed``) and sends one page of them, so paging through the
pages sees each row exactly once. Every name is invented.
"""

from __future__ import annotations

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.routes.pages import KINDS, page
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("review_paging")
    pool = base / "pool"
    for day in ("2024-03-05", "2024-03-06", "2024-03-07"):
        for arm in ("kelt", "vorn"):
            folder = pool / f"{day}_{arm}"
            folder.mkdir(parents=True)
            for index in (1, 2, 3):
                (folder / f"zib_glorbex_{index:02d}.csv").write_text(f"a\n{index}\n")
            (folder / "empty.txt").write_text("")
    made = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(made.path)
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(made, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return made


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_paging_one_row_at_a_time_sees_every_listed_row_once(project: Project, kind: str) -> None:
    with open_catalog(project.path) as catalog:
        whole = page(catalog, project.path, kind, limit=5000)
        seen = []
        offset = 0
        while True:
            one = page(catalog, project.path, kind, limit=1, offset=offset)
            assert one["listed"] == whole["listed"]
            assert one["limit"] == 1
            if not one["rows"]:
                break
            seen.extend(row["target"] for row in one["rows"])
            offset += 1
    assert whole["listed"] == len(whole["rows"])
    assert seen == [row["target"] for row in whole["rows"]]


def test_every_question_says_the_version_of_the_rule_that_raised_it(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        shown = page(catalog, project.path, "questions", limit=5000)
        versions = dict(
            catalog.query("SELECT rule_id, max(version) FROM rules GROUP BY rule_id").fetchall()
        )
    assert shown["rows"], "revision 4 raises questions on the invented pool"
    for row in shown["rows"]:
        if row["rule_id"] in versions:
            assert row["rule_version"] == str(versions[row["rule_id"]])
