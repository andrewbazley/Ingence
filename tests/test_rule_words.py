"""The rules in force in plain words, kept out of everything that is hashed.

A rule should read as a plain explanation of what it means, and a sentence a reader cannot parse is
a defect. A rule's statement enters the Rulebook's digest (``rulebook/resolve.py``), so the plain
sentences live in a table that only the API reads. Held here:

- every shipped rule and every rule of revision 4 has a sentence, free of the engine's own words and
  never ending with its identifier in parentheses;
- the table names no rule that does not exist, so a misspelt key cannot hide a missing one;
- a rule the table has no sentence for shows its statement, never nothing;
- nothing outside the API reads the table, so no identity, digest or schema can depend on it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fixtures.revision_4 import REVISION, write_revision
from ingence.api import plain_words as plain_module
from ingence.api.plain_words import PLAIN_WORDS, PlainWords, plain_words
from ingence.api.routes import rules as rule_routes
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.rulebook.availability import NOT_AVAILABLE
from ingence.rulebook.load import load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

#: Words a biologist should not meet in a rule's plain sentence: the engine's own vocabulary.
JARGON = ("predicate", "scope", "defeater", "defeasible", "abstain", "abstention", "tier")


def _shipped() -> set[str]:
    load_builtins()
    return {rule.rule_id for revision in load_revisions() for rule in revision.rules}


def _revision_4() -> set[str]:
    return {str(declared["id"]) for declared in REVISION["rules"]}  # type: ignore[index, union-attr]


def test_every_shipped_rule_and_every_revision_4_rule_has_a_plain_sentence() -> None:
    table = plain_words().rules
    missing = sorted((_shipped() | _revision_4()) - set(table))
    assert not missing, f"no plain sentence for {missing}"


def test_the_table_names_no_rule_that_does_not_exist() -> None:
    unknown = sorted(set(plain_words().rules) - _shipped() - _revision_4())
    assert not unknown, f"sentences for rules nobody declares: {unknown}"


@pytest.mark.parametrize("rule_id", sorted(plain_words().rules))
def test_a_sentence_names_no_rule_and_speaks_no_jargon(rule_id: str) -> None:
    # The rule's name in parentheses after its plain words is not part of the sentence. The page
    # shows the name and version as the handle that opens the rule.
    sentence = plain_words().rules[rule_id]
    assert f"({rule_id})" not in sentence and sentence.endswith("."), sentence
    assert sentence[0].isupper(), sentence
    spoken = sentence.lower()
    for word in JARGON:
        assert not re.search(rf"\b{word}", spoken), (rule_id, word)
    # 0.4.0 (the website's style guide): one idea per sentence, so a rule may take a few short
    # sentences, and no sentence is over 25 words.
    for part in re.split(r"(?<=[.?!])\s+", sentence):
        assert len(part.split()) <= 25, (rule_id, part)


@pytest.fixture(scope="module")
def inferred(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("rule-words")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn_raw_1.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "vorn_processed_1.csv").write_text("a,b\n3,4\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project


def test_the_rules_page_leads_every_rule_with_its_plain_sentence(inferred) -> None:
    with open_catalog(inferred.path) as catalog:
        shown = rule_routes.rules(catalog)
        stored = catalog.query("SELECT count(*) FROM rules").fetchone()[0]
    listed = [rule for section in shown["sections"] for rule in section["rules"]]
    assert len(listed) == shown["rules"] == stored
    assert all(rule["plain_from"] == "plain words" for rule in listed)
    # A rule that cannot run yet says so first (``rulebook/availability.py``); the sentence follows.
    for rule in listed:
        sentence = plain_words().rules[rule["rule_id"]]
        assert rule["plain"] in (sentence, f"{NOT_AVAILABLE} {sentence}"), rule["rule_id"]
    # Its statement is there too, filled in at the values the run used.
    assert all(rule["statement"] and "{" not in rule["statement"] for rule in listed)
    headings = [section["heading"] for section in shown["sections"]]
    assert headings[:2] == ["What the walk itself records", "What files are"]
    format_rule = next(rule for rule in listed if rule["rule_id"].startswith("format_from"))
    assert format_rule["claims"] == 2 and format_rule["rests_on"] >= 2


def test_a_rule_without_a_sentence_shows_its_statement(inferred, monkeypatch) -> None:
    table = dict(plain_words().rules)
    table.pop("pipeline_stage_from_name")
    monkeypatch.setattr(plain_module, "plain_words", lambda: PlainWords(rules=table))
    with open_catalog(inferred.path) as catalog:
        shown = rule_routes.rules(catalog)
    listed = {rule["rule_id"]: rule for section in shown["sections"] for rule in section["rules"]}
    stage = listed["pipeline_stage_from_name"]
    assert stage["plain_from"] == "statement"
    assert stage["plain"] == stage["statement"]
    assert stage["plain"].startswith("A file sits at the pipeline stage its name states")


def test_nothing_outside_the_api_reads_the_plain_words() -> None:
    """The table enters no rule's identity, no Rulebook digest, no Dictionary fingerprint and no
    exported schema, because nothing that computes one ever opens it."""
    source = Path(plain_module.__file__).resolve().parents[1]
    readers = sorted(
        str(path.relative_to(source))
        for path in source.rglob("*.py")
        if "plain_words" in path.read_text(encoding="utf-8")
        and path.relative_to(source).parts[0] != "api"
    )
    assert readers == [], readers
    assert PLAIN_WORDS.parent.name == "api"
