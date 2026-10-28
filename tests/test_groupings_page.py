"""The Groupings page, top-down: experiments first, then the files no experiment holds.

Grouping has levels. A person answers at the highest level where a group appears, and everything
under it inherits approval as grouped if the top level is approved. Groups distinguish experiments,
not conditions within one. Fine details, like groups of analysis files nested in folders, do not
need the same attention. Held here, over invented names (the experiment rules' own pool,
``test_experiment_folders._pool``):

- each proposed group of experiments is asked first, with its experiments named: the folders the
  clustering rule raised the group's name on;
- a group of files lying wholly inside one experiment is not asked, and is listed under it, written
  as its names with the varying parts in braces and named;
- a group of files spanning two experiments is still asked;
- the Questions page leaves the experiment questions to the Groupings page;
- a name pattern names a counter, a quantity, a variant, a form and a location.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import each_deviation, write_revision
from ingence.api.name_patterns import name_pattern
from ingence.api.routes import pages
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.api.routes.groupings import EXPERIMENT_GROUPS_PREDICATE, experiment_folders
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from test_experiment_folders import _pool

if TYPE_CHECKING:
    from pathlib import Path

DROPLET = "20190304 kelt droplet"
REPEAT = "20190311 kelt droplet repeat"


def _project(base: Path) -> Project:
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    # The clustering's proposals on the page, in the ``each_deviation`` mode. Under the lattice,
    # a proposal of a set of words is asked by the lattice in its turn
    # (``test_group_lattice.py``).
    each_deviation(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return created


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """One project nothing in this module writes to."""
    return _project(tmp_path_factory.mktemp("groupings-page"))


def groupings(project: Project, **filters: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return pages.page(catalog, project.path, "classes", limit=10_000, **filters)


def test_the_proposed_groups_of_experiments_are_asked_first_with_their_experiments(
    shared: Project,
) -> None:
    shown = groupings(shared)
    with open_catalog(shared.path) as catalog:
        raised = {
            value
            for (values,) in catalog.query(
                "SELECT raised FROM findings WHERE rule_id = 'experiments_may_belong_together'"
            ).fetchall()
            for value in values
        }
    levels = [row["level"] for row in shown["rows"]]
    asked = [row for row in shown["rows"] if row["level"] == "experiments"]
    assert levels[: len(asked)] == ["experiments"] * len(asked), "experiments come first"
    assert sorted(row["raised"] for row in asked) == sorted(raised)
    droplet = next(row for row in asked if DROPLET in row["raised"])
    named = {member["path"] for member in droplet["experiments"]}
    assert {DROPLET, REPEAT} <= named
    assert droplet["members"] == len(droplet["experiments"])
    assert droplet["shared"], "what links the experiments is said"
    assert [word["word"] for word in droplet["words"]] == ["one_thing", "mixed", "cannot_say"]
    assert droplet["predicate"] == EXPERIMENT_GROUPS_PREDICATE


def test_a_group_inside_one_experiment_is_shown_under_it_and_not_asked(shared: Project) -> None:
    shown = groupings(shared)
    under = {held["experiment"]: held for held in shown["within_experiments"]}
    assert DROPLET in under
    patterns = {group["pattern"]: group for group in under[DROPLET]["groups"]}
    series = patterns["kelt_vorn_{001,002}.nd2"]
    assert series["dimensions"] == ["counter"]
    assert series["varies"] == "varying by a counter"
    assert series["experiment"] == DROPLET
    asked = {row["target"] for row in shown["rows"]}
    assert not any(group["target"] in asked for group in under[DROPLET]["groups"])
    # Distinct groups: a series listed under each of its experiments counts once.
    assert shown["held_by_experiments"] == len(
        {group["target"] for held in under.values() for group in held["groups"]}
    )
    # Counted toward nothing waiting: the progress counts only the rows asked.
    assert shown["progress"]["rows"] == len(shown["rows"])


def test_a_series_in_two_experiments_is_shown_inside_each_and_not_asked(shared: Project) -> None:
    """No group forms by dates or replicate numbers. The droplet experiment and its repeat both hold
    ``kelt_vorn_001`` and ``002``; the name family they make across the two is a series inside each,
    and is not asked. Whether the two belong together is the lattice's question, asked of the words
    they share (``test_groups_made_of_words.py`` keeps a family varying by a word asked across
    experiments)."""
    shown = groupings(shared)
    spanning = [
        row
        for row in shown["rows"]
        if row["level"] == "files" and row.get("experiments_spanned", 0) >= 2
    ]
    assert spanning == []
    under = {held["experiment"]: held for held in shown["within_experiments"]}
    for experiment in (DROPLET, REPEAT):
        patterns = [group["pattern"] for group in under[experiment]["groups"]]
        assert any(pattern.startswith("kelt_vorn_{001") for pattern in patterns), experiment


def test_the_questions_page_leaves_the_experiment_questions_to_the_groupings_page(
    shared: Project,
) -> None:
    with open_catalog(shared.path) as catalog:
        questions = pages.page(catalog, shared.path, "questions", limit=10_000)
    assert not [
        row for row in questions["rows"] if row["rule_id"] == "experiments_may_belong_together"
    ]
    assert questions["asked_on_groupings"] >= 1


def test_the_experiments_are_the_rules_folders_made_one_set(shared: Project) -> None:
    with open_catalog(shared.path) as catalog:
        found = experiment_folders(catalog)
    droplet = found.by_path[DROPLET]
    assert found.folders[droplet]["kind"] == "experiment"
    assert found.folders[droplet]["path"] == DROPLET
    # Keyed by the folder's entry; its path is kept for showing.
    assert found.of(f"{DROPLET}/reanalysis/kelt_vorn_001.nd2") == droplet
    assert found.path_of(droplet) == DROPLET
    assert found.of("notes/plan.txt") is None


def test_an_experiment_group_answered_leaves_the_page(tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = groupings(project)
    first = next(row for row in before["rows"] if row["level"] == "experiments")
    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog, project.path)
        event = decide_target(
            context,
            Decision(target=first["target"], word="one_thing"),
            actor="kelt",
            occurred_at="2026-09-24T12:00:00Z",
        )
    assert event.kind.value == "confirm"
    assert json.loads(event.payload)["verdict"] == "one_thing"
    after = groupings(project)
    assert first["target"] not in {row["target"] for row in after["rows"]}
    assert after["hidden_answered"] == 1


# ---------------------------------------------------------------------------
# Name patterns.
# ---------------------------------------------------------------------------


def test_a_series_differs_by_a_counter() -> None:
    shaped = name_pattern([f"MAX_kelt_20ms_{index}_zib_vorn.tif" for index in range(2, 12)])
    assert shaped.pattern == "MAX_kelt_20ms_{2-11}_zib_vorn.tif"
    assert shaped.dimensions == ("counter",)


def test_a_number_a_unit_follows_is_a_quantity_and_keeps_its_place() -> None:
    shaped = name_pattern(["zib_10mM_001.nd2", "zib_20mM_001.nd2", "zib_10mM_002.nd2"])
    assert shaped.pattern == "zib_{10,20}mM_{001,002}.nd2"
    assert shaped.dimensions == ("quantity", "counter")
    assert shaped.said == "varying by a counter and a quantity"


def test_a_word_is_a_variant_an_extension_a_form_and_folders_a_location() -> None:
    variant = name_pattern(["kelt_raw_001.csv", "kelt_filtered_001.csv"])
    assert variant.pattern == "kelt_{raw,filtered}_001.csv"
    assert variant.dimensions == ("variant",)
    formed = name_pattern(["kelt_001.tif", "kelt_001.csv", "kelt_002.tif"], folders=2)
    assert formed.pattern == "kelt_{001,002}.{csv,tif}"
    assert formed.dimensions == ("counter", "form", "location")
    assert formed.said == "varying by a counter and a form, in more than one folder"
    same = name_pattern(["vorn.db", "vorn.db"], folders=5)
    assert (same.pattern, same.said) == ("vorn.db", "the same name, in more than one folder")


def test_what_links_a_group_is_read_from_its_reason_or_not_at_all() -> None:
    from ingence.api.routes.groupings import shared_by

    assert shared_by("proposed with 1 other experiments sharing kelt, vorn: zib run") == [
        "kelt",
        "vorn",
    ]
    assert shared_by("proposed with 1 other experiments sharing files: zib run") == []
    assert shared_by("a reason in another shape") == []
