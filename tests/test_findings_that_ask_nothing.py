"""A folder's domain profile and a corrected spelling are findings that ask nothing.

They are recorded, listed by the assumptions route, and never rows of the Questions page. Every
name here is invented."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingence.api.routes import pages
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.rulebook.revision_5.names import ASKS_NOTHING
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("asks_nothing")
    pool = base / "pool" / "scan"
    pool.mkdir(parents=True)
    # A folder of a microscope's files with a slip (confocol) in every name: both rules fire.
    for index in range(1, 25):
        (pool / f"zorb_confocol_{index:02d}.nd2").write_text("x")
    created = Project.create(base / "project", created_at="t", roots=[base / "pool"])
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(
        Project.open(created.path), run_id="run_20260101T000002Z_222222222222", started_at="t"
    )
    return created.path


def test_both_rules_record_findings_and_neither_is_a_question(project: Path) -> None:
    with open_catalog(project) as catalog:
        raised = {
            str(rule): int(count)
            for rule, count in catalog.query(
                "SELECT rule_id, count(*) FROM findings GROUP BY 1"
            ).fetchall()
        }
        rows = pages.page(catalog, project, "questions", limit=1000)["rows"]
    assert raised.get("folder_domain_profile", 0) >= 1
    assert raised.get("spelling_read_as", 0) >= 1
    assert {"folder_domain_profile", "spelling_read_as"} == ASKS_NOTHING
    assert not [row for row in rows if row.get("rule_id") in ASKS_NOTHING]


def test_the_questions_page_counts_the_same_with_the_two_rules_findings_gone(project: Path) -> None:
    """The row count is what it would be had neither rule fired: no row rests on their findings."""
    with open_catalog(project) as catalog:
        rows = pages.page(catalog, project, "questions", limit=1000)["rows"]
        asked = {
            (str(rule), str(value))
            for rule, value in catalog.query(
                "SELECT f.rule_id, u.value FROM findings f, unnest(f.raised) AS u(value) "
                "WHERE f.rule_id NOT IN ('folder_domain_profile', 'spelling_read_as')"
            ).fetchall()
        }
    listed = {
        (str(row.get("rule_id")), str(row.get("raised"))) for row in rows if row.get("rule_id")
    }
    assert listed <= asked | {(rule, value) for rule, value in listed if rule not in ASKS_NOTHING}
    assert all(rule not in ASKS_NOTHING for rule, _ in listed)
