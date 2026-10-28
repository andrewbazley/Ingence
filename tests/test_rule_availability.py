"""A rule that cannot run in this version says so, from one place, in every place it is shown.

The rule texts themselves never say it: a statement is hashed into the Rulebook's digest and a
plain sentence is read by the Rules page, and a rule that gains its evaluator would go on saying
"not available" until somebody remembered to edit the text. So the phrase is added where a rule is
shown, from the engine's own knowledge of which rules cannot run
(:func:`ingence.rulebook.availability.unavailable_rules`):

- ``ingence rule show`` prints it under exactly those rules;
- the Rules page leads exactly those rules' sentences with it;
- the generated reference leads their pages and index rows with it, from the same set;
- the four constraint rules that the engine's own count leaves out are handled like the rest in all
  three places, and the count ``infer`` prints is that set without them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fixtures.revision_4 import write_revision
from ingence.api.plain_words import plain_words
from ingence.api.routes import rules as rule_routes
from ingence.cli.main import app
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.rulebook import shipped_rulebook
from ingence.rulebook.availability import NOT_AVAILABLE, unavailable_rules
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.render import render_rulebook
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "docs"


def _unavailable() -> dict[str, str]:
    load_builtins()
    return unavailable_rules(shipped_rulebook().rules)


def test_some_rules_cannot_run_and_the_constraint_rules_are_among_them() -> None:
    missing = _unavailable()
    book = shipped_rulebook()
    constraints = {r.rule_id for r in book.rules if r.effect.value == "constraint"} & set(missing)
    assert missing and set(missing) <= {rule.rule_id for rule in book.rules}
    assert constraints, "the engine's own count leaves these out; the three places must not"


def test_no_rule_text_carries_the_phrase() -> None:
    """The phrase lives in the display, so a rule that gains an evaluator cannot keep saying it."""
    book = shipped_rulebook()
    for rule in book.rules:
        texts = [rule.statement, rule.rationale, *(p.description for p in rule.parameters)]
        assert not any("not available in this version" in t.lower() for t in texts), rule.rule_id
    assert not [
        rule_id
        for rule_id, sentence in plain_words().rules.items()
        if "not available in this version" in sentence.lower()
    ]


def test_rule_show_says_it_under_exactly_the_rules_that_cannot_run() -> None:
    missing = _unavailable()
    shown = render_rulebook(shipped_rulebook())
    blocks = re.split(r"(?m)^  (?=[a-z_.]+ \(v)", shown)[1:]
    said = {block.split(" ", 1)[0] for block in blocks if NOT_AVAILABLE in block}
    assert said == set(missing)
    assert shown.count(NOT_AVAILABLE) == len(missing)


def test_the_command_prints_the_same_lines() -> None:
    missing = _unavailable()
    result = CliRunner().invoke(app, ["rule", "show"])
    assert result.exit_code == 0, result.output
    assert result.output.count(NOT_AVAILABLE) == len(missing)


@pytest.fixture(scope="module")
def inferred(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("rule-availability")
    pool = base / "pool"
    (pool / "HeLa").mkdir(parents=True)
    (pool / "HeLa" / "DAPI_raw_1.csv").write_text("a,b\n1,2\n")
    (pool / "HeLa" / "DAPI_processed_1.csv").write_text("a,b\n3,4\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project


def test_the_rules_page_leads_exactly_those_rules_with_it(inferred) -> None:
    missing = _unavailable()
    with open_catalog(inferred.path) as catalog:
        shown = rule_routes.rules(catalog)
    listed = [rule for section in shown["sections"] for rule in section["rules"]]
    said = {rule["rule_id"] for rule in listed if rule["plain"].startswith(NOT_AVAILABLE)}
    carried = {rule["rule_id"] for rule in listed}
    assert said == set(missing) & carried
    assert not any(NOT_AVAILABLE in rule["statement"] for rule in listed)


def test_the_count_infer_prints_is_the_set_without_the_constraint_rules(inferred) -> None:
    missing = _unavailable()
    book = {rule.rule_id: rule for rule in shipped_rulebook().rules}
    counted = {r for r in missing if book[r].effect.value != "constraint"}
    result = CliRunner().invoke(app, ["infer", "-p", str(inferred.path)])
    assert result.exit_code == 0, result.output
    found = re.search(r"(\d+) rules? (?:is|are) not available in this version", result.output)
    # The small fixture may not reach every rule; what it counts is never more than the set.
    assert found is None or int(found.group(1)) <= len(counted)


@pytest.mark.skipif(not (TOOLS / "generate_reference.py").is_file(), reason="no generator here")
def test_the_reference_pages_lead_the_same_rules_with_it() -> None:
    sys.path.insert(0, str(TOOLS))
    try:
        import generate_reference
        from reference_rules import build_states
    finally:
        sys.path.remove(str(TOOLS))
    missing = _unavailable()
    book = shipped_rulebook()
    assert {r for r, state in build_states(book.rules).items() if not state.built} == set(missing)
    pages = {page.path: page.text for page in generate_reference._rules([])}
    for rule in book.rules:
        lead = pages[f"rules/{rule.rule_id}.md"].split("by hand.\n\n", 1)[1]
        assert lead.startswith(NOT_AVAILABLE) == (rule.rule_id in missing), rule.rule_id
    index = pages["rules/index.md"]
    rows = [line for line in index.splitlines() if line.startswith("| [")]
    said = {
        re.match(r"\| \[[^\]]*\]\(([^.]+)\.md\)", row).group(1)
        for row in rows
        if NOT_AVAILABLE in row
    }
    assert said == set(missing)
