"""The routes that count experiments count the same ones, and say what they count.

If the routes read experiments differently, they disagree. On the sample collection the files route
read 24 experiments and the themes 17, with 15 in both, and the files of a themes-only experiment
did not load. The files route read the clustering rule's policy (a dated folder name first, then
what a folder holds); the themes and the completeness figures took every folder a rule resolved, the
highest above each file. A person's folder that the structure rule read as binding the experiments
beneath it (they share the names their acquisitions carry) was then one experiment holding all of
them.

The class: one set of experiment folders, the policy's, for every route that counts them, and a
sentence each route can show saying what it counted. The pool is invented: a person's folder
holding three dated folders whose movies share their names.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes.groupings import experiment_folders
from ingence.core.project import Project
from ingence.experiment_units import (
    counted_as,
    counted_folders,
    policy_experiments,
    policy_said,
    run_units,
)
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

DATED = (
    ("2023-03-14", "HeLa DAPI"),
    ("2023-04-02", "HEK293 GFP"),
    ("2023-05-20", "COS7 actin"),
)


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for day, title in DATED:
        folder = pool / "Dana_Whitfield" / f"{day} {title}"
        folder.mkdir(parents=True)
        for number in (1, 2, 3):
            (folder / f"cells_{number:03d}.nd2").write_bytes(bytes([number]) * 64)
    return pool


def _run(created: Project) -> Project:
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return created


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("experiment_definition")
    return _run(Project.create(base / "project", created_at="t", roots=[_pool(base)]))


#: The project's own rule: the structure rule, declared again under a name of its own.
OWN_RULE = "experiment_folder_of_the_lab"


@pytest.fixture(scope="module")
def own_rule_project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    import copy

    import yaml

    from fixtures.revision_4 import REVISION
    from ingence.storage.layout import RULEBOOK_DIR

    base = tmp_path_factory.mktemp("experiment_definition_own_rule")
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    (declared,) = [
        copy.deepcopy(rule)
        for rule in REVISION["rules"]  # type: ignore[attr-defined]
        if rule["id"] == "experiment_folder_from_contents"
    ]
    declared["id"] = OWN_RULE
    directory = created.path / RULEBOOK_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "project-0001.yaml").write_text(
        yaml.safe_dump(
            {
                "project_revision": 1,
                "summary": "dana: the structure rule again, under the lab's own name",
                "changes": [],
                "rules": [declared],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return _run(created)


def _units(project: Project) -> dict[str, str]:
    """The experiments the themes and completeness count: path to entry identifier."""
    with open_catalog(project.path) as catalog:
        found = run_units(catalog)
        return {found.paths[unit]: unit for unit in set(found.units.values())}


def test_the_structure_rules_folder_over_dated_folders_is_not_one_experiment_holding_them(
    project: Project,
) -> None:
    # The structure rule did conclude on the person's folder, as binding the movies beneath it.
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'experiment_folder_from_contents' "
            "AND c.resolution_status = 'resolved'"
        ).fetchall()
    assert [row[0] for row in rows] == ["Dana_Whitfield"]
    # And the dated names, which come first, make three experiments of it.
    assert sorted(_units(project)) == [f"Dana_Whitfield/{day} {title}" for day, title in DATED]


def test_the_themes_and_completeness_count_the_experiments_the_files_route_does(
    project: Project,
) -> None:
    with open_catalog(project.path) as catalog:
        policy = {held["entry_id"] for held in policy_experiments(catalog).values()}
        grouped = {held["entry_id"] for held in experiment_folders(catalog).folders.values()}
    assert set(_units(project).values()) == policy == grouped
    assert len(policy) == len(DATED)


def _ask(project: Path, path: str, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(value)] for name, value in query.items()})


def test_every_experiment_the_themes_count_opens_on_the_files_route(project: Project) -> None:
    for path, unit in _units(project).items():
        status, body = _ask(project.path, "experiments/files", experiment=unit, limit=500)
        assert status == HTTPStatus.OK, (path, body)
        assert body["experiment"]["folder"] == path
        assert body["files"] == 3


def test_a_rule_of_the_projects_own_is_weighed_by_the_policy_and_both_routes_count_alike(
    own_rule_project: Project,
) -> None:
    """A copy of the structure rule under another name concludes on the person's folder too, and the
    themes' count must not fall back to it where the files route keeps the three dated folders."""
    with open_catalog(own_rule_project.path) as catalog:
        concluded = {
            row[0]
            for row in catalog.query(
                "SELECT e.relative_path FROM claims c JOIN entries e ON e.entry_id = c.subject "
                "WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
                [OWN_RULE],
            ).fetchall()
        }
        policy = {held["entry_id"] for held in policy_experiments(catalog).values()}
        grouped = {held["entry_id"] for held in experiment_folders(catalog).folders.values()}
        counted = counted_folders(catalog)
    assert concluded == {"Dana_Whitfield"}
    wanted = [f"Dana_Whitfield/{day} {title}" for day, title in DATED]
    assert sorted(_units(own_rule_project)) == wanted
    # One function decides the set, so the themes, the completeness figures and the files page
    # cannot count apart.
    assert set(_units(own_rule_project).values()) == policy == grouped == set(counted)
    for path, unit in _units(own_rule_project).items():
        status, body = _ask(own_rule_project.path, "experiments/files", experiment=unit, limit=500)
        assert status == HTTPStatus.OK, (path, body)
        assert body["files"] == 3


def test_the_files_route_says_what_it_counted_as_an_experiment(project: Project) -> None:
    unit = next(iter(_units(project).values()))
    _, body = _ask(project.path, "experiments/files", experiment=unit, limit=1)
    said = body["experiment"]["counted_as"]
    assert said.startswith("An experiment is a folder the experiment rules concluded on")
    assert "a dated folder name wins over what a folder holds" in said
    # The files page places a file by the same rule as the themes and completeness, the highest
    # experiment folder above it, so it says what they say.
    assert "A folder inside another experiment is part of it." in said
    assert "nearest" not in said
    with open_catalog(project.path) as catalog:
        assert said == counted_as(catalog)


def test_each_policy_has_a_sentence_and_an_unknown_one_is_named() -> None:
    assert policy_said("name_first") == (
        "Where their folders overlap, a dated folder name wins over what a folder holds."
    )
    assert policy_said("structure_then_name") == (
        "Where their folders overlap, what a folder holds wins over its name."
    )
    assert (
        policy_said("outermost_evidence")
        == "Where their folders overlap, the outermost folder wins."
    )
    assert policy_said("a_later_policy") == (
        "Where their folders overlap, the policy called a later policy decides."
    )


def test_the_themes_summary_says_what_it_counted_as_an_experiment(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sklearn")
    from fixtures.theme_pool import build_theme_project
    from ingence.api import v1

    themed, _ = build_theme_project(tmp_path)
    context = v1.context_for(themed, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "themes", {})
    assert status == HTTPStatus.OK, payload
    said = payload["summary"]["experiments_counted_as"]
    assert said.startswith("An experiment is a folder the experiment rules concluded on")
    assert "A folder inside another experiment is part of it" in said
    assert "A folder inside an installed program is not an experiment." in said
    with open_catalog(themed) as catalog:
        assert said == counted_as(catalog)


def test_a_themes_file_saved_before_the_experiments_changed_is_never_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older project's cached themes counted the old set; the version is in the file's key."""
    from ingence.core.settings import ThemeSettings
    from ingence.themes import projection

    assert projection.PROJECTION_VERSION != "3"
    now = projection._key(ThemeSettings(), "a_dictionary")
    monkeypatch.setattr(projection, "PROJECTION_VERSION", "3")
    assert projection._key(ThemeSettings(), "a_dictionary") != now
