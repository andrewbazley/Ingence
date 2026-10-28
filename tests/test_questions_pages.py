"""The Questions pages' words and answers.

How the Questions pages show and store answers, on an invented fixture:

- a page shows a label, and the Record keeps its word: "cannot say" shows as "Can't say" and
  "empty output" as "Empty or blank output file", with no subtext where the label says it. The
  stored words (``cannot_say``, ``empty_output``) never change;
- a class of copies is answered as a group or not: "Copies", "Unrelated" (``not_copies``, its
  stored meaning kept) and "Can't say". A class of copies does not offer ``related`` ("Related,
  not copies"), but an answer that uses it, as a returned sheet may, is still taken and read;
- a proposed pair may be "Related" (``related``): it belongs together and neither was made from
  the other, a rejection of the derivation, its payload naming ``concerns``;
- precision counts each rejection's word apart, and a sheet imports ``related`` on a pair and on
  a class of copies.

Every name here is invented.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.one_folder_pairs import allow_pairs_in_one_folder
from fixtures.revision_4 import write_revision
from ingence.api.routes import pages
from ingence.api.routes.decide import (
    COMPUTER_READINGS,
    AnswerRefusedError,
    Decision,
    DecisionContext,
    decide_target,
)
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.record.precision import precision_by_rule
from ingence.record.targets import candidate_target, class_target
from ingence.record.verdicts import (
    CANDIDATE_WORDS,
    LABELS,
    RELATED_RELATION,
    SUBTEXTS,
    VERDICTS,
    WORTH_READING,
    class_words,
    shown_word,
    spelled_key,
    word_subtext,
)
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"
MOMENT = "2026-09-30T02:00:00Z"
#: 2020-09-13, one instant shared by three copies of one file.
SHARED_INSTANT = 1_600_000_000
#: 2020-01-01, old enough for revision 4's empty-file rule.
LONG_AGO = 1_577_836_800


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _in_turn(first: Path, second: Path) -> None:
    """The raw file written an hour before the processed one: a pair's direction is the earlier
    file's, by its earliest credible time, and never its stage words."""
    os.utime(first, (LONG_AGO + 3600, LONG_AGO + 3600))
    os.utime(second, (LONG_AGO + 7200, LONG_AGO + 7200))


def _pool(base: Path) -> Path:
    """Copies of one file in three experiments, a raw and processed pair, and an empty file."""
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_vorn", "2024-03-07_zeph"):
        copy = pool / "runs" / folder / "brisa_19_quant.csv"
        _write(copy, "a,b\n1,2\n3,4\n")
        os.utime(copy, (SHARED_INSTANT, SHARED_INSTANT))
    for index in range(3):
        _write(pool / "kelt" / f"vorn_sd_raw_{index}.csv", "a,b\n1,2\n")
        _write(pool / "kelt" / f"vorn_sd_processed_{index}.csv", "a,b\n1,2\n")
        _in_turn(
            pool / "kelt" / f"vorn_sd_raw_{index}.csv",
            pool / "kelt" / f"vorn_sd_processed_{index}.csv",
        )
    _write(pool / "plates" / "raw_plate_alpha.csv", "a,b\n1,2\n")
    _write(pool / "plates" / "processed_plate_alpha.csv", "a,b\n1,2\n")
    _in_turn(pool / "plates" / "raw_plate_alpha.csv", pool / "plates" / "processed_plate_alpha.csv")
    stub = pool / "plates" / "stub.dat"
    stub.write_bytes(b"")
    os.utime(stub, (LONG_AGO, LONG_AGO))
    return pool


def _project(base: Path) -> Project:
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    write_revision(created.path)
    allow_pairs_in_one_folder(created.path)
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFERENCE, started_at="t")
    return created


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """One project nothing in this module writes to: for the reading tests."""
    return _project(tmp_path_factory.mktemp("questions"))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A project of its own, for a test that writes."""
    return _project(tmp_path)


def one(project: Project, sql: str, parameters: list[Any] | None = None) -> Any:
    with open_catalog(project.path) as catalog:
        row = catalog.query(sql, parameters or []).fetchone()
    assert row is not None, sql
    return row[0] if len(row) == 1 else row


def page_of(project: Project, kind: str, **filters: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return pages.page(catalog, project.path, kind, **filters)


def decide(project: Project, **said: Any) -> Any:
    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog, project.path)
        return decide_target(context, Decision(**said), actor="robin", occurred_at=MOMENT)


def payload_of(event: Any) -> dict[str, Any]:
    return json.loads(event.payload)


#: A class of copies: the three files agreeing on name, size and both instants.
COPY_CLASS = (
    "SELECT DISTINCT c.rule_id, c.object_reference FROM claims c JOIN rules r "
    "ON r.rule_id = c.rule_id WHERE r.predicate = 'same_observed_metadata'"
)
#: The plate pair, which is one of the proposed pairs.
PAIR = (
    "SELECT c.candidate_id FROM candidates c JOIN entries s ON s.entry_id = c.source_id "
    "WHERE s.name = 'raw_plate_alpha.csv'"
)


# ---------------------------------------------------------------------------
# The label table: what a page shows, beside the word the Record keeps.
# ---------------------------------------------------------------------------


def test_every_word_a_page_offers_has_one_label_and_other_is_cannot_say() -> None:
    offered = {*VERDICTS, *WORTH_READING, *COMPUTER_READINGS, "not_a_word"}
    assert offered <= set(LABELS), offered - set(LABELS)
    assert LABELS["cannot_say"] == "Can't say"
    assert LABELS["empty_output"] == "Empty or blank output file"
    # A label is a few words, never a sentence, and no two words a row offers read alike.
    assert all(len(label.split()) <= 6 and not label.endswith(".") for label in LABELS.values())
    for words in (CANDIDATE_WORDS, class_words("same_observed_metadata"), class_words(None)):
        shown = [shown_word(word, words) for word in words]
        assert len(set(shown)) == len(shown)
    # A subtext only where the label alone would mislead, and then one short phrase.
    assert set(SUBTEXTS) <= set(LABELS)
    assert all(len(phrase.split()) <= 6 for phrase in SUBTEXTS.values())


def test_a_page_serves_each_word_s_label_and_no_subtext_where_the_label_says_it(
    shared: Project,
) -> None:
    for kind in ("questions", "classes", "claims", "candidates", "extensions"):
        for row in page_of(shared, kind)["rows"]:
            if row.get("rule_id") == "domain_pack_would_read_this_pool":
                # A pack's question says "keep" or "stop loading it" in its own words. The Record
                # still stores accept and refuse.
                continue
            offered = [word["word"] for word in row.get("words") or []]
            for word in row.get("words") or []:
                assert word["label"] == shown_word(word["word"], offered)
                assert word["meaning"] == word_subtext(word["word"], offered)
                assert not word["meaning"] or word["word"] in SUBTEXTS
    offered = {
        word["word"]: word
        for row in page_of(shared, "questions")["rows"]
        for word in row.get("words") or []
    }
    assert offered["cannot_say"]["label"] == "Can't say"
    assert offered["cannot_say"]["meaning"] == ""
    assert offered["empty_output"]["label"] == "Empty or blank output file"
    assert offered["empty_output"]["meaning"] == ""


def test_the_decision_in_force_is_shown_by_its_label_and_stored_by_its_word(
    project: Project,
) -> None:
    rule_id, key = one(project, COPY_CLASS)
    event = decide(project, target=class_target(rule_id, key), word="cannot_say")
    assert payload_of(event)["verdict"] == "cannot_say"
    rows = page_of(project, "classes", answered=True)["rows"]
    (row,) = [row for row in rows if row["target"] == class_target(rule_id, key)]
    assert (row["in_force"]["word"], row["in_force"]["label"]) == ("cannot_say", "Can't say")


# ---------------------------------------------------------------------------
# A class of copies: a group, or not.
# ---------------------------------------------------------------------------


def test_a_class_of_copies_offers_unrelated_and_any_other_class_one_group_or_not() -> None:
    # ``not_copies`` keeps its stored meaning and shows as "Unrelated". The labels are "Copies",
    # "Unrelated" and "Can't say". "Related, not copies" is not offered, and is still read
    # beside "copies".
    assert class_words("same_observed_metadata") == ("copies", "not_copies", "cannot_say")
    assert class_words("same_content_digest") == class_words("same_observed_metadata")
    assert class_words("same_component_values") == ("one_thing", "mixed", "cannot_say")
    copies = class_words("same_observed_metadata")
    shown = [shown_word(word, copies) for word in copies]
    assert shown == ["Copies", "Unrelated", "Can't say"]
    assert shown_word("related", copies) == "Related, not copies"
    # Beside "copies" the label says it all, so no phrase under it. On a pair it keeps its phrase.
    assert word_subtext("related", copies) == ""
    assert shown_word("related", CANDIDATE_WORDS) == "Related"
    assert word_subtext("related", CANDIDATE_WORDS) == SUBTEXTS["related"]
    assert [shown_word(word) for word in class_words(None)] == [
        "One group",
        "Not one group",
        "Can't say",
    ]


def test_related_on_a_class_of_copies_rejects_the_copy_claim_naming_its_word(
    project: Project,
) -> None:
    rule_id, key = one(project, COPY_CLASS)
    row = next(
        row
        for row in page_of(project, "classes")["rows"]
        if row["target"] == class_target(rule_id, key)
    )
    # No longer offered, and still taken where a returned sheet says it.
    assert [word["word"] for word in row["words"]] == ["copies", "not_copies", "cannot_say"]
    assert [word["label"] for word in row["words"]] == ["Copies", "Unrelated", "Can't say"]
    event = decide(project, target=class_target(rule_id, key), word="related")
    assert event.kind is RecordEventKind.REJECT
    assert (payload_of(event)["verdict"], payload_of(event)["relation"]) == ("related", "concerns")
    # ``not_copies`` keeps its stored meaning and kind, shown as "Unrelated".
    again = decide(project, target=class_target(rule_id, key), word="not_copies")
    assert (again.kind, payload_of(again)["verdict"]) == (RecordEventKind.REJECT, "not_copies")
    # ``unrelated`` is not offered on a class, and is refused there.
    with pytest.raises(AnswerRefusedError, match="not something a class"):
        decide(project, target=class_target(rule_id, key), word="unrelated")
    # A class that is not one of copies is not offered the word.
    name_rule, name_key = one(
        project,
        "SELECT DISTINCT rule_id, object_reference FROM claims "
        "WHERE rule_id = 'same_name_identity' AND object_reference LIKE '%plate%'",
    )
    with pytest.raises(AnswerRefusedError, match="not something a class"):
        decide(project, target=class_target(name_rule, name_key), word="related")


# ---------------------------------------------------------------------------
# A proposed pair may be related without either being made from the other.
# ---------------------------------------------------------------------------


def test_related_rejects_the_derivation_and_names_the_relation_it_points_to(
    project: Project,
) -> None:
    candidate_id = one(project, PAIR)
    row = next(
        row
        for row in page_of(project, "candidates")["rows"]
        if row["target"] == candidate_target(candidate_id)
    )
    assert [word["word"] for word in row["words"]] == list(CANDIDATE_WORDS)
    assert "related" in CANDIDATE_WORDS
    event = decide(project, target=candidate_target(candidate_id), word="related")
    assert event.kind is RecordEventKind.REJECT
    said = payload_of(event)
    assert (said["verdict"], said["relation"]) == ("related", RELATED_RELATION)
    assert RELATED_RELATION == "concerns"
    # The answered pair leaves the page, as every answered pair does, and is not asked again.
    assert candidate_target(candidate_id) not in {
        row["target"] for row in page_of(project, "candidates")["rows"]
    }


def test_precision_counts_each_rejection_s_word_apart(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        candidates = [
            str(row[0])
            # The one-file-at-two-steps rule proposes pairs too. This counts the identity's.
            for row in catalog.query(
                "SELECT candidate_id FROM candidates "
                "WHERE rule_id = 'derivation_within_name_identity' ORDER BY candidate_id"
            ).fetchall()
        ]
    assert len(candidates) >= 3
    for candidate_id, word in zip(candidates, ("related", "related", "reversed"), strict=False):
        decide(project, target=candidate_target(candidate_id), word=word)
    with open_catalog(project.path) as catalog:
        versions = dict(catalog.query("SELECT rule_id, version FROM rules").fetchall())
    figure = precision_by_rule(read_events(project.path), versions)[
        "derivation_within_name_identity"
    ]
    assert figure.rejected == 3
    assert figure.rejected_as == (("related", 2), ("reversed", 1))
    assert "rejected as related 2, reversed 1" in figure.sentence()


def test_a_sheet_imports_related_on_a_pair_and_on_a_class_of_copies(
    project: Project, tmp_path: Path
) -> None:
    import csv

    from ingence.record.sheets import import_sheet

    candidate_id = one(project, PAIR)
    rule_id, key = one(project, COPY_CLASS)
    pairs = tmp_path / "pairs.csv"
    classes = tmp_path / "classes.csv"
    with pairs.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["candidate_id", "verdict (derived | reversed | related | unrelated)"])
        writer.writerow([candidate_id, "related"])
    with classes.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["class key", "members", "verdict (copies | related | not_copies | cannot_say)"]
        )
        writer.writerow([spelled_key(key), "3", "related"])
    with open_catalog(project.path) as catalog:
        assert (
            import_sheet(catalog, project.path, pairs, actor="robin", occurred_at=MOMENT).written
            == 1
        )
        assert (
            import_sheet(
                catalog, project.path, classes, actor="robin", occurred_at=MOMENT, rule_id=rule_id
            ).written
            == 1
        )
    by_target = {event.target: payload_of(event) for event in read_events(project.path)}
    assert by_target[candidate_target(candidate_id)]["verdict"] == "related"
    assert by_target[candidate_target(candidate_id)]["relation"] == "concerns"
    assert by_target[class_target(rule_id, key)]["verdict"] == "related"


# ---------------------------------------------------------------------------
# A note's words, read as a name is read. Nothing written until a chip is clicked.
# ---------------------------------------------------------------------------


def _taught(project: Project, word: str, sense: str, axis: str) -> None:
    """Give an invented word a meaning, as a person answering on the Coverage page would."""
    from ingence.api.routes.decide import define_word

    with open_catalog(project.path) as catalog:
        define_word(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            word=word,
            answer=sense,
            theme_kind=axis,
        )


def test_a_note_naming_a_dictionary_term_returns_it_and_reading_writes_nothing(
    project: Project,
) -> None:
    from ingence.api.routes.notes import read_note

    _taught(project, "brisa", "brisa_strain", "organism")
    before = read_events(project.path)
    with open_catalog(project.path) as catalog:
        read = read_note(catalog, project.path, "these look like brisa and vorn tables, from glorp")
    assert read_events(project.path) == before
    terms = {term["text"].casefold(): term for term in read["terms"]}
    assert terms["brisa"]["term"] == "brisa_strain"
    assert terms["brisa"]["theme_kind"] == "organism"
    assert terms["brisa"]["said"].startswith("brisa: an organism")
    # A word nothing binds is a quieter chip where names here carry it, which is where a meaning
    # can be given. An ordinary word no name carries ("look", "glorp") is counted, not a chip.
    unbound = {word["word"].casefold(): word for word in read["unbound"]}
    assert set(unbound) == {"vorn"}
    assert unbound["vorn"]["files"] >= 6
    assert read["unbound_elsewhere"] >= 2
    with open_catalog(project.path) as catalog:
        assert read_note(catalog, project.path, "   ")["terms"] == []


def test_a_chip_s_term_is_kept_as_an_observation_on_the_row_s_own_target(project: Project) -> None:
    rule_id, key = one(project, COPY_CLASS)
    target = class_target(rule_id, key)
    observed = {"text": "brisa", "term": "brisa_strain", "theme_kind": "organism"}
    event = decide(project, target=target, observed=observed)
    assert (event.kind, event.target, event.actor) == (RecordEventKind.NOTE, target, "robin")
    said = payload_of(event)
    assert said["observed"] == [observed]
    assert said["rule_id"] == rule_id
    # A note decides nothing: the row is still waiting, and a verdict after it stands.
    row = next(row for row in page_of(project, "classes")["rows"] if row["target"] == target)
    assert row["in_force"] is None
    # A note is kept on its own, never beside a word, and only about a row the current run holds.
    with pytest.raises(AnswerRefusedError, match="on its own"):
        decide(project, target=target, observed=observed, word="copies")
    with pytest.raises(AnswerRefusedError):
        decide(project, target="class:no_rule:0000", observed=observed)
    with pytest.raises(AnswerRefusedError, match="names each term"):
        decide(project, target=target, observed={"text": "brisa"})


# ---------------------------------------------------------------------------
# Claim samples: one answer for every file a value was concluded about.
# ---------------------------------------------------------------------------


def test_a_value_s_files_are_listed_by_place_each_with_its_claim(shared: Project) -> None:
    rule_id, value, files = one(
        shared,
        "SELECT rule_id, object_reference, count(*) FROM claims "
        "WHERE predicate LIKE 'classifies.%' AND resolution_status = 'resolved' "
        "AND rule_id = 'pipeline_stage_from_name' GROUP BY 1, 2 ORDER BY 3 DESC, 2 LIMIT 1",
    )
    with open_catalog(shared.path) as catalog:
        listed = pages.claim_value(catalog, shared.path, rule_id=rule_id, value=value)
    assert listed["files"] == files == listed["waiting"]
    names = [item for place in listed["places"] for item in place["files"]]
    assert len(names) == files
    assert all(item["target"].startswith("claim:") for item in names)
    assert [place["folder"] for place in listed["places"]] == sorted(
        place["folder"] for place in listed["places"]
    )
    sampled = [row for row in page_of(shared, "claims")["rows"] if row["value"] == value]
    assert sampled and all(row["places_of_value"] == len(listed["places"]) for row in sampled)


def test_one_answer_for_all_writes_an_ordinary_claim_event_per_file_saying_so(
    project: Project,
) -> None:
    from ingence.api.routes.decide import decide_many

    rule_id, value = one(
        project,
        "SELECT rule_id, object_reference FROM claims WHERE rule_id = 'pipeline_stage_from_name' "
        "AND object_reference = 'raw' LIMIT 1",
    )
    with open_catalog(project.path) as catalog:
        listed = pages.claim_value(catalog, project.path, rule_id=rule_id, value=value)
        targets = [item["target"] for place in listed["places"] for item in place["files"]]
        context = DecisionContext(catalog, project.path)
        written, refused = decide_many(
            context,
            [Decision(target=target, word="correct", together=len(targets)) for target in targets],
            actor="robin",
            occurred_at=MOMENT,
        )
    assert refused == []
    assert len(written) == len(targets) > 1
    for event in written:
        said = payload_of(event)
        assert event.kind is RecordEventKind.CONFIRM
        assert (said["verdict"], said["object"]) == ("correct", value)
        assert said["source"]["answered_together"] == len(targets)
    with open_catalog(project.path) as catalog:
        again = pages.claim_value(catalog, project.path, rule_id=rule_id, value=value)
    assert again["waiting"] == 0
    with pytest.raises(AnswerRefusedError, match="positive whole number"):
        Decision.from_body({"target": targets[0], "word": "correct", "together": 0})


def test_a_set_several_rules_formed_is_asked_once_and_answered_on_each_rule(
    project: Project,
) -> None:
    def plates() -> list[dict[str, Any]]:
        return [
            row
            for row in page_of(project, "classes")["rows"]
            if {member["name"] for member in row["listed"]}
            == {"raw_plate_alpha.csv", "processed_plate_alpha.csv"}
        ]

    (row,) = plates()
    assert row["also"], "the same two files, grouped by more than one rule, asked once"
    assert all(other["rule_id"] != row["rule_id"] for other in row["also"])
    # Answered on one rule's class only, the set still waits. On every rule's, it has left.
    decide(project, target=row["target"], word="mixed")
    (still,) = plates()
    assert still["in_force"] is None
    for other in row["also"]:
        decide(project, target=other["target"], word="mixed")
    assert plates() == []


def test_coverage_shows_the_worth_words_by_the_same_labels(shared: Project) -> None:
    from ingence.api.routes.coverage import coverage

    with open_catalog(shared.path) as catalog:
        shown = coverage(catalog, shared.path)
    assert shown["worth_labels"] == {word: LABELS[word] for word in WORTH_READING}


def test_related_on_a_class_of_copies_is_counted_apart_in_precision(project: Project) -> None:
    rule_id, key = one(project, COPY_CLASS)
    decide(project, target=class_target(rule_id, key), word="related")
    with open_catalog(project.path) as catalog:
        versions = dict(catalog.query("SELECT rule_id, version FROM rules").fetchall())
    figure = precision_by_rule(read_events(project.path), versions)[rule_id]
    assert (figure.rejected, figure.rejected_as) == (1, (("related", 1),))


def test_every_kind_of_question_is_headed_in_words_never_by_its_code() -> None:
    from ingence.api.question_words import question_words_for

    said = question_words_for(
        "content_unreadable", "content_unreadable", "link_without_a_target", 1
    )
    assert said["heading"] == "A link to a missing file"
    assert said["lead"] == (
        "This file is empty, or Ingence could not read it. Its results come only from its name "
        "and folder."
    )
    assert said["example"] and "dapi" in said["example"]
    many = question_words_for("content_unreadable", "content_unreadable", "empty", 3)
    assert many["lead"].startswith("These 3 files are empty, or Ingence could not read them.")
    lattice = question_words_for(
        "group_resolution_deviation", "group_resolution_deviation", "a group: kelt vorn", 4
    )
    assert (lattice["heading"], lattice["counted"]) == (
        'Possible group: "kelt vorn"',
        "experiments",
    )
    added = question_words_for("group_resolution_deviation", "x", "a group: kelt vorn + brisa", 2)[
        "heading"
    ]
    assert added == 'Possible addition to the group "kelt vorn": "brisa"'
    missing = question_words_for(
        "group_resolution_deviation", "x", "brisa, missing from the group kelt vorn", 2
    )["heading"]
    assert missing == 'Possible group without "brisa": "kelt vorn"'
    assert (
        question_words_for("miscellaneous_files_to_approve", "x", "notes: miscellaneous files", 1)[
            "heading"
        ]
        == 'Possible miscellaneous files: "notes"'
    )
    assert question_words_for("anything", "x", "load the microscopy pack", 1)["heading"] == (
        "Words for microscopy"
    )
    # A kind the table does not know: its value in words, never a code, led by the rule's sentence.
    unknown = question_words_for("some_flag", "some_rule", "made_by_hand", 2)
    assert unknown["heading"] == "Made by hand"
    assert unknown["counted"] == "files"
    for value in ("empty", "link_outside_the_root", "not_a_regular_file", "empty_output"):
        heading = question_words_for("content_unreadable", "r", value, 1)["heading"]
        assert "_" not in heading


# ---------------------------------------------------------------------------
# Further cases: kept terms, filters, group keys, labels and word questions.
# ---------------------------------------------------------------------------


def test_a_kept_term_is_said_on_the_row_s_record_and_in_history(project: Project) -> None:
    """A term kept from a note is shown on the row, not as "note ...: null"."""
    from ingence.api.routes.changes import _said_of

    rule_id, key = one(project, COPY_CLASS)
    target = class_target(rule_id, key)
    observed = {"text": "brisa", "term": "brisa_strain", "theme_kind": "organism"}
    event = decide(project, target=target, observed=observed)
    row = next(row for row in page_of(project, "classes")["rows"] if row["target"] == target)
    (said,) = row["notes"]
    assert said["note"] == "kept: brisa (an organism)"
    assert said["observed"] == [observed]
    assert _said_of(event)["note"] == "kept: brisa (an organism)"
    # History shows a word by its label: "Can't say", never "cannot say".
    other = decide(project, target=target, word="cannot_say")
    assert _said_of(other)["label"] == "Can't say"


def test_a_set_several_rules_formed_is_found_by_any_of_its_rules(project: Project) -> None:
    """Filtering by the second rule finds the set, and the rule stays in the filter's options."""
    (row,) = [
        row
        for row in page_of(project, "classes")["rows"]
        if {member["name"] for member in row["listed"]}
        == {"raw_plate_alpha.csv", "processed_plate_alpha.csv"}
    ]
    other = row["also"][0]["rule_id"]
    filtered = page_of(project, "classes", rule=other)
    assert row["target"] in {shown["target"] for shown in filtered["rows"]}
    assert other in {item["value"] for item in filtered["filters"]["rules"]}


def test_only_a_group_of_words_is_approved_and_a_value_group_is_no_group(project: Project) -> None:
    """A claim samples' key ``group:<rule>:<json>`` does not reach the group-approval path."""
    from ingence.api.routes.proposals import target_is_proposed
    from ingence.api.v1 import context_for
    from ingence.api.v1.groups import _approved
    from ingence.record.log import append_event

    keyed = 'group:acquisition_stage_from_format:{"stage":"acquisition"}'
    with open_catalog(project.path) as catalog:
        assert target_is_proposed(catalog, project.path, keyed) is not None
        assert target_is_proposed(catalog, project.path, "group:kelt:vorn") is not None
        assert target_is_proposed(catalog, project.path, "group:kelt vorn") is None
    with pytest.raises(AnswerRefusedError, match="names no group"):
        decide(project, target=keyed, word="accept")
    # A confirmation already stored on such a key is not an approved group either.
    append_event(
        project.path,
        kind=RecordEventKind.CONFIRM,
        actor="robin",
        occurred_at=MOMENT,
        target="group:kelt:vorn",
        against_run_id=INFERENCE,
        payload={"verdict": "accept"},
    )
    approved = _approved(context_for(project.path, settled=None, job_running=False))
    assert all(":" not in " ".join(terms) for terms, _ in approved)
    # The claim samples' groups of per-file values carry a display key of their own.
    with open_catalog(project.path) as catalog:
        told = pages._per_file_groups(catalog, [], "earliest_credible_time", True)
    assert told and all(group["target"].startswith("value-group:") for group in told)


def test_a_proposal_s_words_show_labels_and_no_subtext() -> None:
    """A proposal's words show "Accept" and "Refuse", with no phrase restating the question."""
    from ingence.api.routes.proposals import PROPOSAL_WORDS, _shown

    for words in PROPOSAL_WORDS.values():
        for item in _shown(words):
            assert item["label"] in ("Accept", "Refuse")
            assert item["meaning"] == "" and item["explained"]


def test_a_word_question_answered_on_the_word_s_own_target_is_answered(tmp_path: Path) -> None:
    """An answer from the word's page (``term:<word>``) answers its question."""
    from ingence.api.routes.decide import define_word
    from test_word_questions import _inferred, _template

    pool = tmp_path / "pool"
    for folder, extra in (("first", "kelt"), ("second", "kelt"), ("third", "vorn")):
        _template(pool / folder, ("atp", "adp", "tcep", extra))
    created = _inferred(tmp_path, [pool])

    def asked() -> dict[str, Any]:
        rows = page_of(created, "questions", answered=True)["rows"]
        return {row["raised"]: row for row in rows if row.get("word_question")}

    assert asked()["kelt"]["in_force"] is None
    with open_catalog(created.path) as catalog:
        define_word(
            catalog,
            created.path,
            actor="robin",
            occurred_at=MOMENT,
            word="kelt",
            answer="kelt",
            theme_kind="reagent",
        )
    assert asked()["kelt"]["in_force"] is not None
    assert "kelt" not in {row.get("raised") for row in page_of(created, "questions")["rows"]}


# ---------------------------------------------------------------------------
# Bulk answers and imported sheets.
# ---------------------------------------------------------------------------


def test_an_answer_for_all_keeps_a_file_answered_since_the_list_was_shown(
    project: Project,
) -> None:
    """The bulk answer keeps a colleague's answer given between the list and the click."""
    from ingence.api.routes.decide import decide_many

    rule_id, value = one(
        project,
        "SELECT rule_id, object_reference FROM claims WHERE rule_id = 'pipeline_stage_from_name' "
        "AND object_reference = 'raw' LIMIT 1",
    )
    with open_catalog(project.path) as catalog:
        listed = pages.claim_value(catalog, project.path, rule_id=rule_id, value=value)
    targets = [item["target"] for place in listed["places"] for item in place["files"]]
    # A colleague answers one listed file after the list was shown.
    colleague = decide(project, target=targets[0], word="wrong")
    with open_catalog(project.path) as catalog:
        written, refused = decide_many(
            DecisionContext(catalog, project.path),
            [Decision(target=target, word="correct", together=len(targets)) for target in targets],
            actor="trell",
            occurred_at=MOMENT,
        )
    assert len(written) == len(targets) - 1
    assert [(item["target"], item.get("already")) for item in refused] == [(targets[0], "yes")]
    in_force = {event.target: event for event in read_events(project.path)}
    assert in_force[targets[0]].event_id == colleague.event_id


def test_the_sheet_import_refuses_on_a_class_a_word_the_page_refuses(
    project: Project, tmp_path: Path
) -> None:
    """A hand-edited sheet may write ``unrelated`` on a class, which the answer route refuses."""
    import csv

    from ingence.record.sheets import NOT_OFFERED_BY_RULE, import_sheet

    rule_id, key = one(project, COPY_CLASS)
    classes = tmp_path / "classes.csv"
    with classes.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["class key", "members", "verdict"])
        writer.writerow([spelled_key(key), "3", "unrelated"])
    with open_catalog(project.path) as catalog:
        report = import_sheet(
            catalog, project.path, classes, actor="robin", occurred_at=MOMENT, rule_id=rule_id
        )
    assert report.written == 0
    assert NOT_OFFERED_BY_RULE in report.refused
    assert read_events(project.path) == ()
