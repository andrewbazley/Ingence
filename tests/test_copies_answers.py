"""The copies question says why it asks, and answers on copies and pairs count.

A person who sees a set of files grouped because they held identical bytes must be able to tell
why the question was asked. The question and its choices are as clear and simple as possible:

- The copies question leads with its reason, by what formed the set: identical bytes where the
  whole-file digest did, the same sampled bytes where only a sample did, the same name, size and
  last change where the metadata agreement did. The whole-file reason wins where both digests
  formed the set.
- Its answers are "Copies", "Unrelated" and "Can't say". "Related, not copies" is not offered,
  and an answer stored with it reads with its old meaning.
- Inference reads the answers, at the run's Record watermark: a set answered "Unrelated" is
  published as conflicting, defeated by the person's answer and never removed; a set answered
  "Copies" stands as a person's answer; a pair answered anything is not proposed again unless what
  proposed it changed.

Every name here is invented, and every answer is an invented person's (``ines``).
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.one_folder_pairs import allow_pairs_in_one_folder
from ingence.api.routes import pages
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.core.project import Project
from ingence.core.settings import ExtractionSettings
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.record.targets import candidate_target, class_target
from ingence.record.verdicts import class_takes, class_words, shown_word
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.rulebook.resolve import EffectiveRulebook

CENSUS = "run_20260101T000001Z_111111111111"
SAMPLED_DIVE = "run_20260101T000002Z_222222222222"
WHOLE_DIVE = "run_20260101T000003Z_333333333333"
INFERENCE = "run_20260101T000004Z_444444444444"
AGAIN = "run_20260101T000005Z_555555555555"
MOMENT = "2026-09-30T23:00:00Z"
#: 2020-09-13, one instant shared by the three copies, so their metadata agrees.
SHARED_INSTANT = 1_600_000_000
#: The rule a copies set's reason is read from where both digests formed it.
WHOLE_RULE = "identical_by_content_digest"
SAMPLED_RULE = "copies_by_sampled_digest"
METADATA_RULE = "same_observed_metadata_outside_programs"


def rules_in_force(base: Path) -> EffectiveRulebook:
    """The rules in force in the project under ``base``.

    They are the shipped book, with the copy rules among them, and the project's own changes
    beside it."""
    from ingence.rulebook import project_rulebook
    from ingence.rulebook.predicates import load_builtins

    load_builtins()
    book = project_rulebook(base / "project")
    assert SAMPLED_RULE in {rule.rule_id for rule in book.rules}
    return book


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


#: More than a quick digest's two ends (64 KiB each), so a middle can differ unseen by a sample.
_BEYOND_A_SAMPLE = 3 * 64 * 1024


def _pool(base: Path, *, same_names: bool, middles_differ: bool = False) -> Path:
    """Three copies of one table in three experiments, and a raw and processed pair beside them.

    With ``same_names`` the copies share a name, a size and a last change, so the metadata rule
    forms them too; without, only a digest can. With ``middles_differ`` each is long and differs
    from the others only in its middle, which a sample never reads: the same sampled bytes, and
    not identical ones.
    """
    pool = base / "pool"
    for index, folder in enumerate(("2024-03-05_kelt", "2024-03-06_vorn", "2024-03-07_zeph")):
        name = "brisa_19_quant.csv" if same_names else f"brisa_{folder[-4:]}_quant.csv"
        copy = pool / "runs" / folder / name
        if middles_differ:
            half = "a,b\n" + "1" * (_BEYOND_A_SAMPLE // 2)
            _write(copy, f"{half}{index}{half}")
        else:
            _write(copy, "a,b\n1,2\n3,4\n" * 40)
        os.utime(copy, (SHARED_INSTANT, SHARED_INSTANT))
    _write(pool / "plates" / "raw_plate_alpha.csv", "a,b\n1,2\n")
    _write(pool / "plates" / "processed_plate_alpha.csv", "a,b\n1,2\n5,6\n")
    # The raw table an hour before the processed one: a pair's direction is its time.
    for turn, name in enumerate(("raw_plate_alpha.csv", "processed_plate_alpha.csv"), start=1):
        at = SHARED_INSTANT + turn * 3600
        os.utime(pool / "plates" / name, (at, at))
    return pool


def _project(
    base: Path,
    *,
    same_names: bool = True,
    dives: tuple[str, ...] = (),
    middles_differ: bool = False,
) -> Project:
    """A project over the pool: a census, the dives named (``standard``, ``deep``), an inference."""
    pool = _pool(base, same_names=same_names, middles_differ=middles_differ)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    # The raw and processed pair lies in one folder, which a name identity leaves as siblings;
    # these tests are about answers on a pair, so the pair is let be ordered there.
    allow_pairs_in_one_folder(project.path)
    book = rules_in_force(base)
    if dives:
        project = project.save_settings(
            project.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
        )
    run_census(project, run_id=CENSUS, started_at="t")
    for profile in dives:
        run_id = WHOLE_DIVE if profile == "deep" else SAMPLED_DIVE
        run_deepen(project, run_id=run_id, started_at="t", profile=profile, rulebook=book)
    run_inference(project, run_id=INFERENCE, started_at="t", rulebook=book)
    return project


def infer_again(project: Project, base: Path, *, run_id: str = AGAIN, book: Any = None) -> None:
    run_inference(project, run_id=run_id, started_at="t", rulebook=book or rules_in_force(base))


def page_of(project: Project, kind: str, **filters: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return pages.page(catalog, project.path, kind, **filters)


def decide(project: Project, **said: Any) -> Any:
    """Answer through the one answer route, as a page does."""
    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog, project.path)
        return decide_target(context, Decision(**said), actor="ines", occurred_at=MOMENT)


def copies_row(project: Project, **filters: Any) -> dict[str, Any]:
    """The Groupings page's row for the three copies of the table."""
    (row,) = [
        row
        for row in page_of(project, "classes", **filters)["rows"]
        if row.get("level") == "files"
        and {member["name"].rpartition("_")[2] for member in row["listed"]} == {"quant.csv"}
    ]
    return row


def answer_row(project: Project, row: dict[str, Any], word: str) -> None:
    """Answer a set on every rule's class, as the page's one answer does."""
    for target in [row["target"], *(other["target"] for other in row.get("also") or ())]:
        decide(project, target=target, word=word)


# ---------------------------------------------------------------------------
# 16. The copies question says why it asks, and offers three answers.
# ---------------------------------------------------------------------------


def test_a_copies_set_is_offered_copies_unrelated_and_other() -> None:
    for predicate in ("same_content_digest", "same_observed_metadata"):
        offered = class_words(predicate)
        assert offered == ("copies", "not_copies", "cannot_say")
        assert [shown_word(word, offered) for word in offered] == [
            "Copies",
            "Unrelated",
            "Can't say",
        ]
        # Not offered, and still taken where a returned sheet says it.
        assert "related" not in offered
        assert class_takes(predicate) == ("copies", "not_copies", "cannot_say", "related")
    # Any other class is one group or not.
    assert class_words("same_component_values") == class_takes("same_component_values")


def test_a_stored_related_on_a_copies_set_reads_with_its_old_meaning(tmp_path: Path) -> None:
    project = _project(tmp_path)
    row = copies_row(project)
    assert [word["word"] for word in row["words"]] == ["copies", "not_copies", "cannot_say"]
    event = decide(project, target=row["target"], word="related")
    said = json.loads(event.payload)
    assert (said["verdict"], said["relation"]) == ("related", "concerns")
    # The page, and the Changes page, say it as it was offered: "Related, not copies".
    answered = copies_row(project, answered=True)
    assert answered["in_force"]["label"] == "Related, not copies"
    from ingence.api.routes.changes import _said_of

    assert _said_of(event)["label"] == "Related, not copies"


@pytest.mark.parametrize(
    ("same_names", "middles_differ", "dives", "rule", "why"),
    [
        (True, False, (), METADATA_RULE, "These 3 files have the same name, size and last change"),
        (False, False, ("deep",), WHOLE_RULE, "These 3 files hold identical bytes"),
        (
            False,
            True,
            ("standard",),
            SAMPLED_RULE,
            "These 3 files hold the same bytes in the parts Ingence read",
        ),
    ],
)
def test_the_copies_question_leads_with_what_formed_the_set(
    tmp_path: Path,
    same_names: bool,
    middles_differ: bool,
    dives: tuple[str, ...],
    rule: str,
    why: str,
) -> None:
    project = _project(tmp_path, same_names=same_names, dives=dives, middles_differ=middles_differ)
    row = copies_row(project)
    assert row["rule_id"] == rule
    assert row["why"] == why


def test_where_both_digests_formed_the_set_the_whole_file_reason_is_shown(
    tmp_path: Path,
) -> None:
    # A standard dive of a small file computes both digests, as a laboratory's dive does.
    project = _project(tmp_path, same_names=False, dives=("standard",))
    row = copies_row(project)
    assert row["rule_id"] == WHOLE_RULE
    assert [other["rule_id"] for other in row["also"]] == [SAMPLED_RULE]
    assert row["why"] == "These 3 files hold identical bytes"
    # One answer from the pane still writes to every rule's class.
    answer_row(project, row, "copies")
    written = {event.target for event in read_events(project.path)}
    assert {row["target"], row["also"][0]["target"]} <= written
    assert class_target(WHOLE_RULE, row["key"]) == row["target"]


# ---------------------------------------------------------------------------
# 17. Answers that count: read by inference, at the run's Record watermark.
# ---------------------------------------------------------------------------

#: The rule reading a person's copies answers.
ANSWER_RULE = "copies_a_person_answered"
THIRD = "run_20260101T000006Z_666666666666"
#: The rule concluding the stages a derivation pair's support cites.
STAGE_RULE = "pipeline_stage_from_name"


def _targets(row: dict[str, Any]) -> list[str]:
    return [row["target"], *(other["target"] for other in row.get("also") or ())]


def _class_claims(project: Project, target: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """Each claim into the class a target names: its status and its defeaters."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT rule_id, object_reference, resolution_status, defeaters_json FROM claims "
            "WHERE predicate LIKE 'groups.%' AND object_reference IS NOT NULL"
        ).fetchall()
    return [
        (str(status), json.loads(defeaters or "[]"))
        for rule_id, key, status, defeaters in rows
        if class_target(str(rule_id), str(key)) == target
    ]


def _waiting_copies(project: Project) -> list[dict[str, Any]]:
    return [
        row
        for row in page_of(project, "classes")["rows"]
        if row.get("level") == "files"
        and {member["name"].rpartition("_")[2] for member in row["listed"]} == {"quant.csv"}
    ]


#: The rules a person's "Unrelated" may defeat: a claim that can be retracted. The whole-file
#: digest's claim is entailed, a fact the answer grants ("different files that happen to agree").
RETRACTABLE = ("defeasible", "abductive")


@pytest.mark.parametrize(
    ("same_names", "dives"), [(True, ()), (False, ("standard",))], ids=["metadata", "digests"]
)
def test_a_set_answered_unrelated_is_published_as_conflicting_and_asked_no_more(
    tmp_path: Path, same_names: bool, dives: tuple[str, ...]
) -> None:
    project = _project(tmp_path, same_names=same_names, dives=dives)
    row = copies_row(project)
    answer_row(project, row, "not_copies")
    said = {event.target: event for event in read_events(project.path)}
    infer_again(project, tmp_path)
    tiers = _tiers(project)
    defeated = 0
    for rule_id, target in zip(
        [row["rule_id"], *(other["rule_id"] for other in row.get("also") or ())],
        _targets(row),
        strict=True,
    ):
        claims = _class_claims(project, target)
        assert len(claims) == 3
        for status, defeaters in claims:
            person = [item for item in defeaters if item["rule_id"] == ANSWER_RULE]
            if tiers[rule_id] in RETRACTABLE:
                # Defeated by the person's answer, published as conflicting, never removed.
                assert status == "examined_conflicting"
                assert [(item["fired"], item["observations"]) for item in person] == [
                    (True, [said[target].event_id])
                ]
                defeated += 1
            else:
                # An entailed claim is a fact the answer grants: it stands, undefeated.
                assert (status, person) == ("resolved", [])
    assert defeated == 3
    # The page asks it no more, and still says why it was asked, answered "Unrelated".
    assert _waiting_copies(project) == []
    answered = copies_row(project, answered=True)
    assert answered["in_force"]["label"] == "Unrelated"
    assert answered["why"].startswith("These 3 files ")
    with open_catalog(project.path) as catalog:
        grouped = catalog.query(
            "SELECT count(*) FROM claims WHERE rule_id = ?", [ANSWER_RULE]
        ).fetchone()
    assert grouped == (0,)


def _tiers(project: Project) -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        return {
            str(rule_id): str(tier)
            for rule_id, tier in catalog.query("SELECT rule_id, tier FROM rules").fetchall()
        }


def test_a_set_answered_copies_stands_as_a_persons_answer(tmp_path: Path) -> None:
    # A small file's standard dive: both digest rules form the set, answered once for both.
    project = _project(tmp_path, same_names=False, dives=("standard",))
    row = copies_row(project)
    assert row["also"]
    answer_row(project, row, "copies")
    infer_again(project, tmp_path)
    with open_catalog(project.path) as catalog:
        claimed = catalog.query(
            "SELECT e.name, c.object_reference, c.resolution_status FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject WHERE c.rule_id = ?",
            [ANSWER_RULE],
        ).fetchall()
    # One set, the three files, keyed by the set a person answered, standing.
    assert sorted(name for name, _, _ in claimed) == [
        "brisa_kelt_quant.csv",
        "brisa_vorn_quant.csv",
        "brisa_zeph_quant.csv",
    ]
    assert {key for _, key, _ in claimed} == {min(_targets(row))}
    assert {status for _, _, status in claimed} == {"resolved"}
    # The copy rules' own claims stand beside it, and the person's set is never asked back.
    for target in _targets(row):
        assert {status for status, _ in _class_claims(project, target)} == {"resolved"}
    shown = page_of(project, "classes", answered=True)
    asked_rules = {rule for item in shown["rows"] for rule in [item["rule_id"]]}
    held_rules = {
        group["rule_id"] for held in shown.get("within_experiments", []) for group in held["groups"]
    }
    assert ANSWER_RULE not in asked_rules | held_rules
    assert copies_row(project, answered=True)["in_force"]["label"] == "Copies"


def test_an_answer_is_read_at_the_run_s_record_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.record.answers import answers_read, events_at

    project = _project(tmp_path)
    row = copies_row(project)
    first = decide(project, target=row["target"], word="copies")
    second = decide(project, target=row["target"], word="not_copies")
    then = answers_read(events_at(project.path, first.sequence), watermark=first.sequence)
    assert then.copies[row["target"]].word == "copies"
    now = answers_read(events_at(project.path, second.sequence), watermark=second.sequence)
    assert now.copies[row["target"]].word == "not_copies"
    # A run whose watermark came before the second answer reads the first, and says so.
    monkeypatch.setattr("ingence.infer.run.record_watermark", lambda _: first.sequence)
    infer_again(project, tmp_path)
    assert {status for status, _ in _class_claims(project, row["target"])} == {"resolved"}
    with open_catalog(project.path) as catalog:
        assert catalog.query("SELECT record_watermark FROM run").fetchone() == (first.sequence,)
    monkeypatch.undo()
    infer_again(project, tmp_path, run_id=THIRD)
    assert {status for status, _ in _class_claims(project, row["target"])} == {
        "examined_conflicting"
    }


#: The plate pair, which the name-identity derivation proposes. The two names differ only
#: by a step word, so the one-file-at-two-steps rule proposes the pair too.
#: The query leaves that rule out.
PAIR = (
    "SELECT c.candidate_id, c.rule_id FROM candidates c JOIN entries s ON s.entry_id = c.source_id "
    "JOIN entries t ON t.entry_id = c.target_id "
    "WHERE s.name LIKE '%plate_alpha.csv' AND t.name LIKE '%plate_alpha.csv' "
    "AND c.rule_id <> 'same_file_later_step'"
)


def _pairs(project: Project) -> list[tuple[str, str]]:
    with open_catalog(project.path) as catalog:
        return [(str(a), str(b)) for a, b in catalog.query(PAIR).fetchall()]


def _declared_again(book: EffectiveRulebook, named: str, **update: str) -> EffectiveRulebook:
    """The rules with one declared again: at another version, or under another name."""
    return dataclasses.replace(
        book,
        rules=tuple(
            rule.model_copy(update=update) if rule.rule_id == named else rule for rule in book.rules
        ),
    )


def test_an_answered_pair_is_not_proposed_again_on_the_evidence_answered(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    ((candidate_id, rule_id),) = _pairs(project)
    event = decide(project, target=candidate_target(candidate_id), word="unrelated")
    stored = json.loads(event.payload)["evidence"]
    assert stored["rule_id"] == rule_id
    assert stored["support"], "what the proposing rule read travels with the answer"
    # The very proposal again: kept, answered, not waiting.
    infer_again(project, tmp_path)
    assert _pairs(project) == [(candidate_id, rule_id)]
    assert candidate_target(candidate_id) not in {
        row["target"] for row in page_of(project, "candidates")["rows"]
    }
    # The rule declared again at another version names the pair anew, on the same evidence: it
    # is not proposed again.
    book = _declared_again(rules_in_force(tmp_path), rule_id, version="99")
    infer_again(project, tmp_path, run_id=THIRD, book=book)
    assert _pairs(project) == []


def test_a_pair_is_proposed_again_where_what_proposed_it_changed(tmp_path: Path) -> None:
    """The same rule and two files: left out on the reading answered, asked on a new one."""
    project = _project(tmp_path)
    ((candidate_id, rule_id),) = _pairs(project)
    decide(project, target=candidate_target(candidate_id), word="unrelated")
    renamed = _declared_again(rules_in_force(tmp_path), rule_id, version="99")
    # The rule declared again names the pair anew; what it read is what was answered: left out.
    infer_again(project, tmp_path, run_id=THIRD, book=renamed)
    assert _pairs(project) == []
    # What it read changes: the stages it cites are concluded by another rule. The same
    # proposing rule proposes the pair again, and it is asked.
    read_anew = _declared_again(renamed, STAGE_RULE, rule_id=f"{STAGE_RULE}_again")
    infer_again(project, tmp_path, run_id="run_20260101T000009Z_999999999999", book=read_anew)
    ((again, proposer),) = _pairs(project)
    assert (again != candidate_id, proposer) == (True, rule_id)
    assert candidate_target(again) in {
        row["target"] for row in page_of(project, "candidates")["rows"]
    }


def test_another_rule_proposing_an_answered_pair_is_asked(tmp_path: Path) -> None:
    project = _project(tmp_path)
    ((candidate_id, rule_id),) = _pairs(project)
    decide(project, target=candidate_target(candidate_id), word="unrelated")
    # Another rule proposing the pair is other evidence: the pair is asked again.
    book = _declared_again(rules_in_force(tmp_path), rule_id, rule_id=f"{rule_id}_again")
    infer_again(project, tmp_path, book=book)
    ((again, proposer),) = _pairs(project)
    assert (again != candidate_id, proposer) == (True, f"{rule_id}_again")


def test_a_pair_s_evidence_is_what_its_claims_concluded_not_their_names() -> None:
    from ingence.core.schema import SupportComponent
    from ingence.record.answers import AnswersRead, PairAnswer, pair_evidence, unanswered_pairs

    def support(*cited: str) -> tuple[SupportComponent, ...]:
        return (SupportComponent(name="stage", family="stage", value=1.0, observations=cited),)

    spelled = {"claim_a": "kelt stage_rule raw", "claim_b": "kelt stage_rule raw"}
    # A claim named anew (another version of the rule) concluding the same is the same evidence.
    assert pair_evidence("r", support("claim_a"), spelled) == pair_evidence(
        "r", support("claim_b"), spelled
    )
    answered = pair_evidence("r", support("claim_a"), spelled)
    from ingence.core.ids import canonical_json

    answers = AnswersRead(
        watermark=1,
        pairs={
            frozenset({"kelt", "vorn"}): (
                PairAnswer(
                    candidate_id="cand_one",
                    word="unrelated",
                    ends=frozenset({"kelt", "vorn"}),
                    rule_id="r",
                    evidence=canonical_json(answered),
                ),
            )
        },
    )

    def candidate(identifier: str, cited: str) -> Any:
        return dataclasses.make_dataclass(
            "Proposed", ["candidate_id", "source_id", "target_id", "rule_id", "support"]
        )(identifier, "vorn", "kelt", "r", support(cited))

    same = candidate("cand_two", "claim_b")
    other = candidate("cand_three", "claim_c")
    spelled["claim_c"] = "kelt stage_rule processed"
    kept, left_out = unanswered_pairs(
        [same, other], answers, lambda wanted: {key: spelled[key] for key in wanted}
    )
    assert ([item.candidate_id for item in kept], left_out) == (["cand_three"], 1)


def test_the_answers_inference_reads_are_told_apart_for_the_re_run_list(tmp_path: Path) -> None:
    """What waits for Re-run counts the answers the next run reads, keyed by target prefix."""
    from ingence.record.answers import inference_reads

    project = _project(tmp_path)
    row = copies_row(project)
    ((candidate_id, _),) = _pairs(project)
    copies = decide(project, target=row["target"], word="copies")
    other = decide(project, target=row["target"], word="cannot_say")
    pair = decide(project, target=candidate_target(candidate_id), word="cannot_say")
    assert (inference_reads(copies), inference_reads(other), inference_reads(pair)) == (
        True,
        False,
        True,
    )


def test_the_very_pair_answered_is_asked_again_where_its_evidence_changed(
    tmp_path: Path,
) -> None:
    """The same proposal, answered on other evidence than the evidence it rests on, waits again."""
    from ingence.record.verdicts import Subject, candidate_subjects, current_run, write_verdict

    project = _project(tmp_path)
    ((candidate_id, _),) = _pairs(project)
    target = candidate_target(candidate_id)
    decide(project, target=target, word="unrelated")
    assert target not in {row["target"] for row in page_of(project, "candidates")["rows"]}
    # The answer as it would stand had the pair been proposed on a reading since replaced.
    with open_catalog(project.path) as catalog:
        subject = candidate_subjects(catalog, [candidate_id])[candidate_id]
        run = current_run(catalog)
    earlier = {**subject.about, "evidence": {"rule_id": subject.rule_id, "support": []}}
    write_verdict(
        project.path,
        Subject(subject.target, subject.rule_id, subject.rule_version, earlier),
        "unrelated",
        actor="ines",
        occurred_at=MOMENT,
        against_run_id=run,
    )
    assert target in {row["target"] for row in page_of(project, "candidates")["rows"]}


def test_a_returned_sheet_saying_related_on_a_copies_set_is_taken_where_the_route_takes_it(
    tmp_path: Path,
) -> None:
    """Groupings' Import plans what the answer route would take: ``related`` still, on copies."""
    import csv
    import io

    from ingence.api.routes import workspace as workspace_routes
    from ingence.record.verdicts import spelled_key

    project = _project(tmp_path)
    row = copies_row(project)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["class key", "members", "verdict (copies | related | not_copies)"])
    writer.writerow([spelled_key(str(row["key"])), "3", "related"])
    shown = workspace_routes.write(
        project.path,
        "/api/workspace/sheet-preview",
        {"document": out.getvalue(), "name": "copies.csv", "actor": "tomas"},
    )
    assert [(item["target"], item["word"]) for item in shown["planned"]] == [
        (row["target"], "related")
    ]


@pytest.mark.parametrize("word", ["derived", "reversed"])
def test_a_pair_confirmed_and_renamed_stays_published_answered(tmp_path: Path, word: str) -> None:
    """A pair a person confirmed a direction of is not lost when its rule is renamed."""
    project = _project(tmp_path)
    ((candidate_id, rule_id),) = _pairs(project)
    decide(project, target=candidate_target(candidate_id), word=word)
    book = _declared_again(rules_in_force(tmp_path), rule_id, version="99")
    infer_again(project, tmp_path, run_id=THIRD, book=book)
    ((again, _),) = _pairs(project)
    assert again != candidate_id
    shown = page_of(project, "candidates", answered=True)["rows"]
    (row,) = [row for row in shown if row["target"] == candidate_target(again)]
    assert row["in_force"]["word"] == word
    assert candidate_target(again) not in {
        row["target"] for row in page_of(project, "candidates")["rows"]
    }
    # The Graph draws it with the person's answer too.
    from ingence.api.v1 import V1Context
    from ingence.api.v1.explorer.lineage import _Line, _proposal_decisions

    line = _Line(
        edge_id=again,
        source=str(row["source_id"]),
        target=str(row["target_id"]),
        primitive="derives",
        proposed=True,
        rule_id=rule_id,
        rule_version="99",
        tier=None,
        resolution=None,
        candidate_id=again,
    )
    decided = _proposal_decisions(V1Context(project.path, None, None, False), [line])
    assert decided[again][0] == ("confirm" if word == "derived" else "reject")


@pytest.mark.parametrize("word", ["not_copies", "copies"])
def test_an_answer_on_a_set_is_about_the_files_shown_and_a_newcomer_is_asked(
    tmp_path: Path, word: str
) -> None:
    """A file joining the set after the answer is not a file the person was shown."""
    project = _project(tmp_path)
    row = copies_row(project)
    answer_row(project, row, word)
    said = json.loads(read_events(project.path)[-1].payload)
    with open_catalog(project.path) as catalog:
        named = {
            str(entry): str(name)
            for entry, name in catalog.query(
                "SELECT entry_id, name FROM entries "
                "WHERE entry_id IN (SELECT unnest(?::VARCHAR[]))",
                [said["members"]],
            ).fetchall()
        }
    assert sorted(named.values()) == ["brisa_19_quant.csv"] * 3
    # A fourth copy arrives in a new experiment, agreeing on name, size and last change.
    newcomer = tmp_path / "pool" / "runs" / "2024-03-08_trell" / "brisa_19_quant.csv"
    _write(newcomer, "a,b\n1,2\n3,4\n" * 40)
    os.utime(newcomer, (SHARED_INSTANT, SHARED_INSTANT))
    run_census(project, run_id="run_20260101T000007Z_777777777777", started_at="t")
    infer_again(project, tmp_path, run_id="run_20260101T000008Z_888888888888")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.subject, c.rule_id, c.resolution_status, c.defeaters_json FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject WHERE e.name = 'brisa_19_quant.csv' "
            "AND c.predicate LIKE 'groups.%'",
        ).fetchall()
    shown = set(said["members"])
    for subject, rule_id, status, defeaters in rows:
        person = [item for item in json.loads(defeaters or "[]") if item["rule_id"] == ANSWER_RULE]
        if rule_id == ANSWER_RULE:
            assert subject in shown, "only the files a person answered are grouped as their answer"
        elif word == "not_copies":
            assert bool(person) == (subject in shown)
            assert status == ("examined_conflicting" if subject in shown else "resolved")
    if word == "copies":
        assert sum(1 for _, rule_id, _, _ in rows if rule_id == ANSWER_RULE) == 3
    # The set of four waits again: the newcomer is asked about, and the row says why.
    (waiting,) = _waiting_copies(project)
    assert waiting["members"] == 4
    label = "Unrelated" if word == "not_copies" else "Copies"
    assert waiting["answered_before"] == {"label": label, "files": 3}


def test_the_graph_no_longer_draws_a_set_a_person_called_unrelated(tmp_path: Path) -> None:
    """The lineage's groups leave out a claim a person's "Unrelated" retracted."""
    from ingence.api.v1.explorer.lineage import _groups

    project = _project(tmp_path)
    row = copies_row(project)
    answer_row(project, row, "not_copies")
    infer_again(project, tmp_path)
    shown = json.loads(read_events(project.path)[-1].payload)["members"]
    with open_catalog(project.path) as catalog:
        groups, edges = _groups(catalog, shown)
    assert row["rule_id"] not in {edge.rule_id for edge in edges}
    assert all(group.members_total < 3 or "metadata" not in group.label for group in groups)


def test_a_confirmation_is_carried_only_to_a_proposal_the_same_way_round() -> None:
    """A confirmation of "Made from it" on kelt to vorn never marks vorn to kelt."""
    from ingence.core.ids import canonical_json
    from ingence.record.answers import AnswersRead, PairAnswer, carried_confirmations

    evidence = {"rule_id": "r", "support": []}
    answers = AnswersRead(
        watermark=1,
        pairs={
            frozenset({"kelt", "vorn"}): (
                PairAnswer(
                    candidate_id="cand_one",
                    word="derived",
                    ends=frozenset({"kelt", "vorn"}),
                    rule_id="r",
                    evidence=canonical_json(evidence),
                    source="kelt",
                    target="vorn",
                ),
            )
        },
    )
    carried = carried_confirmations(
        [("cand_two", "kelt", "vorn", "r"), ("cand_three", "vorn", "kelt", "r")],
        answers,
        {"cand_two": evidence, "cand_three": evidence},
    )
    assert carried == {"cand_two": (candidate_target("cand_one"),)}


def test_only_a_set_of_copies_keeps_the_files_it_was_answered_on(tmp_path: Path) -> None:
    """A group answered "One group" takes in a new replicate unasked."""
    project = _project(tmp_path)
    (other,) = [
        row
        for row in page_of(project, "classes")["rows"]
        if row.get("level") == "files"
        and not any(word["word"] == "copies" for word in row["words"])
    ][:1]
    event = decide(project, target=other["target"], word="one_thing")
    assert "members" not in json.loads(event.payload)
