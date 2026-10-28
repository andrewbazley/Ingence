"""What ``/api/rules`` serves for the Rules table and its card.

Beside each rule's sentence and counts, the route says what the rule decides and reads (the words
the website's rule pages use: ``rulebook/describe.py``), what would defeat it, and the run the
counts were taken in. For a rule out of force, the route also says who switched it off: a person in
the project's own rules, the engine's own shipping, or a newer rule that replaced it. The page shows
"off" in magenta only for the first. Every name is a shipped rule, and the pool is invented.
"""

from __future__ import annotations

from typing import Any

import pytest

from ingence.api.routes import rules as rule_routes
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.rulebook import shipped_rulebook
from ingence.rulebook.describe import EFFECTS, decides, reads
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import RULEBOOK_DIR


def _switched_off() -> str:
    for rule in shipped_rulebook().rules:
        if not rule.parameters and rule.status.value != "provisional":
            return str(rule.rule_id)
    pytest.skip("every shipped rule has settings")


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], str]:
    base = tmp_path_factory.mktemp("rules_route_card")
    pool = base / "pool"
    for folder in ("2024-03-05 HeLa DAPI", "2024-03-06 HeLa DAPI"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"HeLa_DAPI_{index:02d}.nd2").write_text("x")
    made = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z")
    run_inference(
        made, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    named = _switched_off()
    folder = made.path / RULEBOOK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project-0001.yaml").write_text(
        f"project_revision: 1\nsummary: Off.\nchanges:\n  - disable: {named}\n", encoding="utf-8"
    )
    with open_catalog(made.path) as catalog:
        return rule_routes.rules(catalog, made.path), named


def test_every_rule_says_what_it_decides_and_reads_in_the_words_the_website_uses(
    served: tuple[dict[str, Any], str],
) -> None:
    data, _ = served
    kinds = {phrase for phrase, _ in EFFECTS.values()}
    rules = [rule for section in data["sections"] for rule in section["rules"]]
    assert rules
    book = {rule.rule_id: rule for rule in shipped_rulebook().rules}
    for rule in rules:
        assert rule["reads"], rule["rule_id"]
        assert isinstance(rule["defeated_by"], list)
        assert rule["heading"]
        # The same functions the website's rule page calls say it ("What it concludes" there).
        assert rule["decides"] == decides(book[rule["rule_id"]]), rule["rule_id"]
        assert rule["reads"] == reads(book[rule["rule_id"]]), rule["rule_id"]
    # A rule naming no relation is said by its kind of output; one naming one, by the relation.
    plain = [rule for rule in rules if book[rule["rule_id"]].primitive is None]
    related = [rule for rule in rules if book[rule["rule_id"]].primitive is not None]
    assert plain and related
    assert all(rule["decides"] in kinds for rule in plain)
    assert all(
        rule["decides"].startswith(book[rule["rule_id"]].primitive.value) for rule in related
    )


def test_the_run_the_counts_were_taken_in_is_named(served: tuple[dict[str, Any], str]) -> None:
    data, _ = served
    assert data["run"]["number"] == 2
    assert data["run"]["label"].startswith("Scan 2")


def test_a_rule_a_project_switched_off_is_said_to_be_off_by_a_person(
    served: tuple[dict[str, Any], str],
) -> None:
    data, named = served
    by_id = {rule["rule_id"]: rule for rule in data["inactive"]}
    assert by_id[named]["off_kind"] == "person"
    assert by_id[named]["reason"] == "switched off in your project's rules"
    # It ran before the project switched it off, so the run shown still carries its counts.
    assert by_id[named]["in_this_run"] is True
    assert by_id[named]["effect"] == next(
        rule.effect.value for rule in shipped_rulebook().rules if rule.rule_id == named
    )
    # A rule a newer one replaced is not a person's doing.
    replaced = [rule for rule in data["inactive"] if rule["off_kind"] == "replaced"]
    assert replaced
    assert all(rule["reason"].startswith("replaced") for rule in replaced)
    assert {rule["off_kind"] for rule in data["inactive"]} <= {"person", "replaced", "shipped"}


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("disabled at project revision 3", "person"),
        ("superseded at project revision 2", "replaced"),
        ("superseded at revision 4", "replaced"),
        ("disabled at revision 2", "shipped"),
        ("disabled", "shipped"),
    ],
)
def test_who_switched_a_rule_off_is_read_from_the_rulebook_s_own_reason(
    reason: str, kind: str
) -> None:
    assert rule_routes.off_kind(reason) == kind
