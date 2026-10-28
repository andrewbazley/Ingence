"""The question sheet as the engine's own output, the pack question first.

The sheet has two properties:

- a question whose answer changes the words other questions are asked in is asked before them. A
  vocabulary pack loaded changes the words a group question names, so the pack's question leads
  the Questions page and the sheet, ahead of the group questions asked first;
- every question on the sheet settles a class and says so: its kind, the class it settles (rule,
  value, how many), three examples drawn reproducibly from the class's own key, how many claims or
  pairs one answer settles, and, for a pattern of derivations, the name pattern with its varying
  parts marked. There is no "trend vote": a tied word stays a person's question.

The pool is invented: thirty pairs of invented words, each pair the name of two dated folders of
instrument files a project loading no microscopy pack cannot read, so the engine proposes the pack
and the lattice asks thirty sets of words.
"""

from __future__ import annotations

import datetime as dt
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from fixtures.revision_4 import tune, write_revision
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

#: Invented syllables. Every word built from them binds to nothing in the shipped Dictionary.
_HEADS = (
    *("zor", "quel", "vex", "brin", "tolm", "sarv"),
    *("klep", "drub", "fosk", "gorv", "hult", "jask"),
)
_TAILS = ("ik", "op", "un", "ab", "ey")
WORDS = [head + tail for head in _HEADS for tail in _TAILS]
#: Thirty pairs, each the name of two dated folders.
PAIRS = [(WORDS[2 * index], WORDS[2 * index + 1]) for index in range(30)]


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _build(base: Path) -> Project:
    pool = base / "pool"
    day = dt.date(2024, 1, 1)
    for first, second in PAIRS:
        for _ in range(2):
            folder = pool / f"{day:%Y%m%d} {first} {second}"
            for number in (1, 2):
                _write(folder / f"{first}_{second}_{number:02d}.nd2")
            day += dt.timedelta(days=1)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    settings = yaml.safe_load((created.path / "settings.yaml").read_text())
    # The microscopy pack is asked about, not accepted by default for its ND2 files.
    settings.setdefault("dictionary", {}).update(
        domain_packs="named", domain_packs_named=["programs"], files_to_accept_a_pack=0
    )
    (created.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    write_revision(created.path)
    # Two experiments make a set worth asking here (the default is five).
    tune(created.path, "minimum_experiments", 2)
    project = Project.open(created.path)
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_000000000002", started_at="t")
    return project


@pytest.fixture(scope="module")
def pool(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return _build(tmp_path_factory.mktemp("sheet"))


def _sheet(project: Project, **query: Any) -> dict[str, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project.path, settled=None, job_running=False)
    status, body = dispatch(context, "questions/sheet", {k: [str(v)] for k, v in query.items()})
    assert status == HTTPStatus.OK, body
    return body


def _page(project: Project) -> list[dict[str, Any]]:
    from ingence.api.routes import pages

    with open_catalog(project.path) as catalog:
        return list(pages.page(catalog, project.path, "questions", limit=500)["rows"])


def test_each_audit_settles_a_class_with_three_examples_drawn_from_its_key(pool) -> None:
    body = _sheet(pool)
    audits = [question for question in body["questions"] if question["kind"] == "audit"]
    assert audits
    for question in audits:
        assert question["rule_id"] and question["value"] is not None
        assert question["settles"] == "claims"
        assert question["settles_n"] == question["size"]
        assert len(question["examples"]) == min(3, question["size"])
        assert question["target"].startswith("class:" + question["rule_id"] + ":")
        assert question["key"] == question["target"]
    again = {q["key"]: q["examples"] for q in _sheet(pool)["questions"] if q["kind"] == "audit"}
    assert again == {question["key"]: question["examples"] for question in audits}
    # Questions are numbered in order, nothing over the budget.
    numbers = [question["n"] for question in body["questions"]]
    assert numbers == list(range(1, len(numbers) + 1))
    assert len(numbers) <= 50
    small = _sheet(pool, budget=1)
    assert len(small["questions"]) == 1  # no pack question: every domain loads


def test_no_question_carries_a_trend_vote_and_the_summary_counts_what_answering_settles(
    pool,
) -> None:
    body = _sheet(pool)
    for question in body["questions"]:
        assert "vote" not in question["question"].lower()
        assert "trend" not in question["question"].lower()
    summary = body["summary"]
    audits = [question for question in body["questions"] if question["kind"] == "audit"]
    assert summary["audits"] == len(audits)
    assert summary["packs"] == 0  # every domain loads
    assert summary["claims_settled"] == sum(question["settles_n"] for question in audits)
    assert 0 <= summary["claims_settled"] <= summary["classifying_claims"]
    assert summary["audit_classes_total"] >= summary["audits"]
    assert 1 <= summary["classes_for_95pc"] <= summary["audit_classes_total"]


def test_a_pattern_marks_the_parts_of_a_name_that_vary() -> None:
    from ingence.review.question_sheet import name_template

    assert name_template("zeph_007.nd2", "tracks_zeph_007.csv") == "tracks_{src}.csv"
    assert name_template("zeph_007.nd2", "summary_2.xlsx") == "(no stem) summary_#.xlsx"
    assert name_template("Kelt_12.TIF", "kelt_12_mask_3.tif") == "{src}_mask_#.tif"


def test_three_examples_are_drawn_by_the_class_key_whatever_order_the_members_come_in() -> None:
    from ingence.review.question_sheet import drawn_examples

    members = [f"brisa/vorn_{number:03d}.csv" for number in range(40)]
    drawn = drawn_examples("class:kelt_rule:abc", members)
    assert len(drawn) == 3
    assert drawn == drawn_examples("class:kelt_rule:abc", list(reversed(members)))
    assert drawn != drawn_examples("class:kelt_rule:abd", members) or len(set(members)) <= 3
    assert drawn_examples("class:kelt_rule:abc", members[:2]) == sorted(members[:2])


def test_derivation_patterns_are_asked_with_their_name_pattern(explorer_project) -> None:
    """On the explorer fixture (invented names): proposed derivations from raw files to their
    masks and tables, each pattern one question settling its pairs."""
    from ingence.api.v1 import context_for, dispatch

    context = context_for(explorer_project.project, settled=None, job_running=False)
    status, body = dispatch(context, "questions/sheet", {})
    assert status == HTTPStatus.OK, body
    patterns = [q for q in body["questions"] if q["kind"] == "derivation pattern"]
    assert patterns, "the fixture proposes derivations named for their acquisitions"
    for question in patterns:
        assert "{src}" in question["template"] or question["template"].startswith("(no stem)")
        assert question["settles"] == "pairs"
        assert question["settles_n"] == question["size"] >= 1
        assert 1 <= len(question["examples"]) <= 3
        assert all(" -> " in example for example in question["examples"])
    summary = body["summary"]
    assert summary["patterns_total"] >= len(patterns)
    assert summary["pairs"] >= sum(question["settles_n"] for question in patterns)
    assert 0 <= summary["pairs_settled"] <= summary["pairs"]


def test_a_loaded_pack_s_question_never_says_it_is_not_loaded() -> None:
    """The keep question's lead is not the proposing rule's sentence ("a pack you have not
    loaded"), which would be said of a pack that is loaded. Its words say what it asks."""
    from ingence.api.question_words import question_words_for

    kept = question_words_for(
        "domain_pack_would_read", "domain_pack_would_read_this_pool", "keep the microscopy pack", 1
    )
    assert kept["heading"] == "Words for microscopy: not used here"
    assert "not loaded" not in kept["lead"]
    assert "microscopy" in kept["lead"] and kept["lead"].endswith(".")
    proposed = question_words_for(
        "domain_pack_would_read", "domain_pack_would_read_this_pool", "load the microscopy pack", 1
    )
    assert proposed["heading"] == "Words for microscopy"
    assert proposed["lead"][0].isupper() and proposed["lead"].endswith(".")
