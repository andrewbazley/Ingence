"""Doubt never withholds content.

If there are files in a folder, doubt about the folder does not prevent its scan or the inclusion
of its contents in analysis. The doubt rules raise a finding about a folder's name and nothing in
the census, the dive or inference reads it. This holds that as a constraint, over the grammar:
every file beneath a doubted folder is censused, dived and inferred on exactly as the same file
beneath a folder nobody doubted.

Every name here is invented.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from fixtures.one_folder_pairs import allow_pairs_in_one_folder
from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    pass

DOUBTED = ("untitled folder", "New Folder With Items", "bad drift vorn")
TWIN = "kelt series"
FILES = ("vorn_brisa_raw.csv", "vorn_brisa_processed.csv", "vorn notes.txt")


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("doubt")
    pool = base / "pool"
    for folder in (*DOUBTED, TWIN):
        (pool / folder).mkdir(parents=True)
        for turn, name in enumerate(FILES, start=1):
            (pool / folder / name).write_text("a,b\n1,2\n" if name.endswith(".csv") else "a note")
            # Each file an hour after the one before: a pair's direction is its time.
            os.utime(pool / folder / name, (1_583_064_000 + turn * 3600,) * 2)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    # Its pairs lie in one folder, which the identity leaves as siblings.
    allow_pairs_in_one_folder(created.path)
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    run_inference(created, run_id="run_20260101T000003Z_333333333333", started_at="t")
    return created


def _by_folder(project: Project, sql: str) -> dict[str, list[tuple]]:
    found: dict[str, list[tuple]] = {}
    with open_catalog(project.path) as catalog:
        for folder, *rest in catalog.query(sql).fetchall():
            found.setdefault(str(folder), []).append(tuple(rest))
    return {folder: sorted(rows) for folder, rows in found.items()}


def test_the_doubt_rules_raised_each_doubted_folder(project: Project) -> None:
    raised = _by_folder(
        project,
        "SELECT e.name, f.rule_id FROM findings f JOIN entries e "
        "ON e.entry_id = f.subject OR 'folder:' || e.entry_id = f.subject "
        "WHERE f.rule_id LIKE 'doubt%'",
    )
    assert set(raised) == set(DOUBTED)


def test_every_file_beneath_a_doubted_folder_is_censused_and_dived(project: Project) -> None:
    """Listed, and opened by the dive, exactly as its twin beneath a folder nobody doubted."""
    outcomes = _by_folder(
        project,
        "SELECT p.name, e.name, e.scan_outcome FROM entries e JOIN entries p "
        "ON p.entry_id = e.parent_entry_id WHERE e.entry_type = 'file'",
    )
    for folder in DOUBTED:
        assert outcomes[folder] == outcomes[TWIN], folder
    assert all(outcome != "metadata_only" for _, outcome in outcomes[TWIN])


def _without_the_instant(reference: object) -> object:
    """A claim's value with any instant it names left out, keeping where the instant came from.

    The twins' files are written a moment apart, so their creation times can fall either side
    of a second boundary (Windows did); the time's source is what must agree.
    """
    try:
        parsed = json.loads(str(reference))
    except ValueError:
        return reference
    if isinstance(parsed, dict) and "at" in parsed:
        return json.dumps({key: value for key, value in parsed.items() if key != "at"})
    return reference


def test_every_file_beneath_a_doubted_folder_is_inferred_on_as_its_twin(project: Project) -> None:
    """The same claims at the same standing, and no doubt among any claim's defeaters."""
    claims = {
        folder: sorted(
            (name, rule, _without_the_instant(reference), status)
            for name, rule, reference, status in rows
        )
        for folder, rows in _by_folder(
            project,
            "SELECT p.name, e.name, c.rule_id, c.object_reference, c.resolution_status "
            "FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "JOIN entries p ON p.entry_id = e.parent_entry_id "
            "WHERE e.entry_type = 'file' AND c.predicate NOT LIKE 'groups.%'",
        ).items()
    }
    for folder in DOUBTED:
        assert claims[folder] == claims[TWIN], folder
    with open_catalog(project.path) as catalog:
        cited = catalog.query(
            "SELECT count(*) FROM claims WHERE defeaters_json LIKE '%doubt_in_a_folder_name%' "
            "OR defeaters_json LIKE '%doubt_to_confirm%'"
        ).fetchone()[0]
    assert cited == 0


def test_a_doubted_folder_s_files_group_and_are_proposed_as_any_others(project: Project) -> None:
    """Classes form and pairs are proposed beneath doubt as beneath its twin."""
    grouped = _by_folder(
        project,
        "SELECT p.name, c.rule_id, count(*) FROM claims c JOIN entries e ON e.entry_id = c.subject "
        "JOIN entries p ON p.entry_id = e.parent_entry_id "
        "WHERE c.predicate LIKE 'groups.%' AND c.resolution_status = 'resolved' GROUP BY 1, 2",
    )
    proposed = _by_folder(
        project,
        "SELECT p.name, c.rule_id FROM candidates c JOIN entries e ON e.entry_id = c.source_id "
        "JOIN entries p ON p.entry_id = e.parent_entry_id",
    )
    for folder in DOUBTED:
        assert grouped.get(folder) == grouped.get(TWIN), folder
        assert proposed.get(folder) == proposed.get(TWIN), folder
    assert proposed.get(TWIN)
