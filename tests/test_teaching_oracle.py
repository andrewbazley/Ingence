"""The oracle that teaches the engine its words from the answer key.

The oracle's promises are held on a pool of a few files named with invented spellings of real words.
It answers the first ``k`` rows of the queue it is given (a word and the spellings the Words page
offers beside it), from the key's words family, as the key. It writes the event a person's answer to
a word is. It skips and counts a word the key does not cover. The answers read back as a person's
and bind after a rebind. The measurement runs a project for each ``k`` by the same path, for the
tier-on collection and the tier-off one.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

import pytest

from ingence.api.routes.decide import define_word
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.core.settings import DictionarySettings, Settings
from ingence.dictionary.case_reading import Reading, project_answers
from ingence.evaluation.__main__ import main
from ingence.evaluation.key import (
    AnswerKey,
    PairFamily,
    ValueFamily,
    WordMeaning,
    WordsFamily,
    load_key,
)
from ingence.evaluation.score import (
    APPROVER,
    PAGE_STATES,
    Teaching,
    WordAnswer,
    WordsRow,
    key_lines,
    measure_teaching,
    measure_teaching_arms,
    rows_of_queue,
    summary,
    taught_summary,
    teach_words,
    words_queue,
    words_queue_rows,
    words_rows,
)
from ingence.record.log import read_events
from ingence.record.questions import terms_in_force
from ingence.scan.census import run_census
from ingence.scan.reading_figure import reading_figure
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

RUN = "run_20260101T000001Z_111111111111"
REBOUND = "run_20260101T000002Z_222222222222"

#: What the key says. "tublin" is a slip of "tubulin", "hoeschst" of "Hoechst" (which the
#: Dictionary reads), "tax-3" a code made from "taxol", "tubhoe" two words run together, and "zork"
#: a spelling the key gives two meanings. "zorbex" is in no key.
SPELLINGS = {
    "tubulin": WordMeaning("tubulin", "tubulin", "reagent", False, ("original",)),
    "tublin": WordMeaning("tubulin", "tubulin", "reagent", False, ("slip",)),
    "hoeschst": WordMeaning("Hoechst", "nuclear_stain", "reagent", True, ("slip",)),
    "hoechst": WordMeaning("Hoechst", "nuclear_stain", "reagent", True, ("original",)),
    "tax-3": WordMeaning("taxol", "taxol", "reagent", False, ("code",)),
    "tubhoe": WordMeaning(
        "tubulin Hoechst",
        "tubulin_hoechst",
        "subject",
        False,
        ("glued",),
        (("tubulin", "reagent"), ("nuclear_stain", "reagent")),
    ),
    "zork": WordMeaning("zork", "zork_a", "reagent", False, ("original",)),
    "zork-2": WordMeaning("zork", "zork_b", "reagent", False, ("code",)),
}
KEY = AnswerKey(families={}, words=WordsFamily(spellings=SPELLINGS))
#: The files of the pool, the word each carries, and how many files carry it.
FILES = {
    "tubulin": 3,
    "tublin": 2,
    "hoeschst": 1,
    "hoechst": 1,
    "tax-3": 1,
    "tubhoe": 1,
    "zork": 1,
    "zorbex": 1,
}


def _project(tmp_path: Path, name: str = "project") -> Project:
    pool = tmp_path / f"{name}-pool"
    (pool / "day_one").mkdir(parents=True)
    for word, count in FILES.items():
        for index in range(1, count + 1):
            (pool / "day_one" / f"plate_{word}_{index}.csv").write_text("a,b\n")
    project = Project.create(
        tmp_path / name,
        created_at="t",
        roots=[pool],
        settings=Settings(dictionary=DictionarySettings(domain_packs="all")),
    )
    run_census(project, run_id=RUN, started_at="t")
    return project


def _pool(pool: Path) -> Path:
    (pool / "day_one").mkdir(parents=True)
    for word, count in FILES.items():
        for index in range(1, count + 1):
            (pool / "day_one" / f"plate_{word}_{index}.csv").write_text("a,b\n")
    return pool


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return _project(tmp_path)


def _unknown(project: Project) -> int:
    with open_catalog(project.path) as catalog:
        return int(reading_figure(catalog)["words"]["unknown"])


def test_the_default_queue_lists_the_unbound_words_most_files_first(project: Project) -> None:
    queue = words_queue(project.path)

    assert queue[:2] == ("tubulin", "tublin")
    assert {"tax", "tubhoe", "zork", "zorbex"} <= set(queue)
    # A slip of a Dictionary spelling is read as that spelling, marked corrected, and asked
    # about nowhere.
    assert "hoeschst" not in queue
    # A word the Dictionary reads, and a number, are no word to be taught.
    assert "hoechst" not in queue and "3" not in queue
    assert len(queue) == len(set(queue))


def test_the_oracle_writes_the_expected_events_for_a_tiny_key_at_k_2(project: Project) -> None:
    taught = teach_words(project.path, KEY, ["tubulin", "tublin", "hoeschst"], 2)

    assert [(a.word, a.event, a.term) for a in taught.answered] == [
        ("tubulin", "define", "tubulin"),
        ("tublin", "assign", "tubulin"),
    ]
    assert (taught.k, taught.looked_at, taught.skipped) == (2, 2, {})
    events = read_events(project.path)
    assert [(e.kind, e.actor, e.target) for e in events] == [
        (RecordEventKind.DEFINE, APPROVER, "term:tubulin"),
        (RecordEventKind.ASSIGN, APPROVER, "term:tublin"),
    ]
    assert APPROVER == "answer_key"
    payloads = [json.loads(e.payload) for e in events]
    assert [(p["raised"], p["answer"], p["theme_kind"]) for p in payloads] == [
        ("tubulin", "tubulin", "reagent"),
        ("tublin", "tubulin", "reagent"),
    ]
    assert payloads[0]["as_shown"] == {"files": 3}
    assert payloads[1]["as_shown"] == {"files": 2}


def test_a_spelling_of_a_word_the_dictionary_holds_is_an_assignment_to_its_term(
    project: Project,
) -> None:
    taught = teach_words(project.path, KEY, ["hoeschst"], 1)

    [event] = read_events(project.path)
    assert event.kind is RecordEventKind.ASSIGN
    assert json.loads(event.payload)["answer"] == "nuclear_stain"
    assert [a.term for a in taught.answered] == ["nuclear_stain"]


def test_a_word_the_key_lacks_is_skipped_counted_and_uses_a_place(project: Project) -> None:
    taught = teach_words(project.path, KEY, ["zorbex", "tublin"], 1)

    assert (taught.looked_at, len(taught.answered), taught.skipped) == (
        1,
        0,
        {"not in the key": 1},
    )
    assert not read_events(project.path)


def test_k_can_count_the_words_answered_instead_of_the_words_looked_at(project: Project) -> None:
    taught = teach_words(project.path, KEY, ["zorbex", "tublin", "tubulin"], 1, count_skipped=False)

    assert [a.word for a in taught.answered] == ["tublin"]
    assert (taught.looked_at, taught.skipped) == (2, {"not in the key": 1})


def test_a_word_the_dictionary_already_places_is_left_alone(project: Project) -> None:
    taught = teach_words(project.path, KEY, ["hoechst"], 1)

    assert taught.skipped == {"the Dictionary already places it": 1}
    assert not read_events(project.path)


def test_a_word_the_key_gives_two_meanings_is_left_alone(project: Project) -> None:
    taught = teach_words(project.path, KEY, ["zork"], 1)

    assert taught.skipped == {"the key gives it two meanings": 1}
    assert not read_events(project.path)


def test_a_code_is_answered_as_its_letters(project: Project) -> None:
    taught = teach_words(project.path, KEY, ["tax"], 1)

    [event] = read_events(project.path)
    assert event.target == "term:tax"
    assert json.loads(event.payload)["answer"] == "taxol"
    assert [a.shapes for a in taught.answered] == [("code",)]


def test_a_spelling_of_a_name_of_two_words_is_a_sense_of_its_own_or_its_first_words(
    tmp_path: Path,
) -> None:
    own = _project(tmp_path, "own")
    first = _project(tmp_path, "first")

    teach_words(own.path, KEY, ["tubhoe"], 1)
    teach_words(first.path, KEY, ["tubhoe"], 1, pairs="first")

    assert json.loads(read_events(own.path)[0].payload)["answer"] == "tubulin_hoechst"
    assert json.loads(read_events(own.path)[0].payload)["theme_kind"] == "subject"
    assert json.loads(read_events(first.path)[0].payload)["answer"] == "tubulin"
    assert json.loads(read_events(first.path)[0].payload)["theme_kind"] == "reagent"
    with pytest.raises(ValueError, match="pairs is one of"):
        teach_words(own.path, KEY, ["tubhoe"], 1, pairs="second")


def test_a_key_with_no_words_family_gives_the_oracle_nothing_to_answer(project: Project) -> None:
    taught = teach_words(project.path, AnswerKey(families={}), ["tubulin"], 1)

    assert taught.skipped == {"not in the key": 1}
    assert not read_events(project.path)


def test_the_oracle_writes_the_event_the_define_control_writes(tmp_path: Path) -> None:
    by_oracle = _project(tmp_path, "oracle")
    by_route = _project(tmp_path, "route")
    words = [
        ("tubulin", "tubulin", "reagent"),
        ("tublin", "tubulin", "reagent"),
        ("tax", "taxol", "reagent"),
    ]

    teach_words(by_oracle.path, KEY, [word for word, _, _ in words], 3)
    with open_catalog(by_route.path) as catalog:
        for word, answer, kind in words:
            define_word(
                catalog,
                by_route.path,
                actor="answer_key",
                occurred_at="2026-01-01T01:00:00Z",
                word=word,
                answer=answer,
                theme_kind=kind,
            )

    ours, theirs = read_events(by_oracle.path), read_events(by_route.path)
    assert len(ours) == len(theirs) == 3
    for ours_event, theirs_event in zip(ours, theirs, strict=True):
        assert (ours_event.kind, ours_event.target, ours_event.scope, ours_event.actor) == (
            theirs_event.kind,
            theirs_event.target,
            theirs_event.scope,
            theirs_event.actor,
        )
        ours_payload, theirs_payload = (
            json.loads(ours_event.payload),
            json.loads(theirs_event.payload),
        )
        # The names carrying the word are counted as the Define control counts them.
        for field in ("raised", "answer", "theme_kind", "as_shown"):
            assert ours_payload[field] == theirs_payload[field], field


def test_the_answers_read_back_as_a_person_s_and_bind_after_a_rebind(project: Project) -> None:
    before = _unknown(project)
    teach_words(project.path, KEY, ["tubulin", "tublin", "hoeschst"], 3)

    answers = terms_in_force(project.path)
    assert sorted((a.raised, a.term, a.theme_kind, a.rule_id) for a in answers) == [
        ("hoeschst", "nuclear_stain", "reagent", ""),
        ("tublin", "tubulin", "reagent", ""),
        ("tubulin", "tubulin", "reagent", ""),
    ]
    report = run_census(project, run_id=REBOUND, started_at="t", rebind=True)
    assert report.published
    # Five occurrences were unknown (3 + 2) and are understood now. The slip hoeschst was read as
    # hoechst before it was taught.
    assert before - _unknown(project) == 5
    with open_catalog(project.path) as catalog:
        bound = catalog.query(
            "SELECT DISTINCT c.normalized_value, b.term FROM dictionary_bindings b "
            "JOIN components c USING (component_id) "
            "WHERE c.normalized_value IN ('tubulin', 'tublin', 'hoeschst')"
        ).fetchall()
    # The two spellings of one word are one sense; the slip of a word the Dictionary holds is
    # that word's.
    assert {(word, term) for word, term in bound} == {
        ("tubulin", "tubulin"),
        ("tublin", "tubulin"),
        ("hoeschst", "nuclear_stain"),
    }


def test_a_line_of_work_is_a_connected_set_of_the_key_s_group_family() -> None:
    key = AnswerKey(
        families={
            "experiment_folder": ValueFamily(
                expected={
                    "a": "experiment",
                    "b": "experiment",
                    "c": "experiment",
                    "d": "experiment",
                    "a/raw": None,
                }
            ),
            "group": PairFamily(directed=False, expected=(("a", "b"), ("b", "c"))),
        }
    )

    lines = key_lines(key)

    assert set(lines) == {"a", "b", "c", "d"}
    assert lines["a"] == lines["b"] == lines["c"] != lines["d"]


def test_a_row_is_answered_as_a_batch_and_its_same_thing_spellings_name_the_words_term(
    project: Project,
) -> None:
    row = WordsRow("tubulin", ("tublin", "zorbex", "hoechst"))

    taught = teach_words(project.path, KEY, [row], 1)

    assert [(a.word, a.event, a.term, a.same_as) for a in taught.answered] == [
        ("tubulin", "define", "tubulin", None),
        ("tublin", "assign", "tubulin", "tubulin"),
    ]
    assert (taught.k, taught.looked_at, taught.rows_answered) == (1, 1, 1)
    assert taught.skipped == {
        "not in the key": 1,
        "the Dictionary already places it": 1,
    }
    events = read_events(project.path)
    assert [(e.kind, e.actor, e.target) for e in events] == [
        (RecordEventKind.DEFINE, APPROVER, "term:tubulin"),
        (RecordEventKind.ASSIGN, APPROVER, "term:tublin"),
    ]
    assert [json.loads(e.payload)["answer"] for e in events] == ["tubulin", "tubulin"]
    shown = taught.to_json()
    assert (shown["answered"], shown["answered_same_thing"], shown["rows_answered"]) == (2, 1, 1)


def test_k_counts_rows_so_a_row_with_spellings_uses_one_place(project: Project) -> None:
    queue = [WordsRow("tubulin", ("tublin",)), WordsRow("hoeschst"), WordsRow("tax")]

    taught = teach_words(project.path, KEY, queue, 2)

    assert [a.word for a in taught.answered] == ["tubulin", "tublin", "hoeschst"]
    assert (taught.looked_at, taught.rows_answered) == (2, 2)
    assert len(read_events(project.path)) == 3


def test_a_spelling_the_key_calls_another_thing_is_answered_with_its_own_term(
    project: Project,
) -> None:
    taught = teach_words(project.path, KEY, [WordsRow("tubulin", ("hoeschst",))], 1)

    assert [(a.word, a.term, a.same_as) for a in taught.answered] == [
        ("tubulin", "tubulin", None),
        ("hoeschst", "nuclear_stain", None),
    ]
    assert taught.to_json()["answered_same_thing"] == 0


def test_a_row_the_key_lacks_uses_a_place_unless_k_counts_the_rows_answered(
    project: Project,
) -> None:
    queue = [WordsRow("zorbex", ("zork-2",)), WordsRow("tublin")]

    by_looking = teach_words(project.path, KEY, queue, 1)
    assert (by_looking.looked_at, by_looking.rows_answered, len(by_looking.answered)) == (1, 0, 0)

    by_answering = teach_words(project.path, KEY, queue, 1, count_skipped=False)
    assert (by_answering.looked_at, by_answering.rows_answered) == (2, 1)
    assert [a.word for a in by_answering.answered] == ["tublin"]


def test_a_row_for_a_word_an_earlier_row_answered_is_hidden_and_uses_no_place(
    project: Project,
) -> None:
    queue = [
        WordsRow("tubulin", ("tublin",)),
        WordsRow("tublin", ("tubulin",)),
        WordsRow("hoeschst"),
    ]

    taught = teach_words(project.path, KEY, queue, 2)

    # The page hides an answered word, so the second row is not there: the third is the second.
    assert [a.word for a in taught.answered] == ["tubulin", "tublin", "hoeschst"]
    assert (taught.looked_at, taught.rows_answered) == (2, 2)
    assert taught.skipped == {"already answered in this round": 1}
    assert len(read_events(project.path)) == 3


def test_the_words_the_page_serves_are_read_as_rows() -> None:
    served = {
        "rows": [
            {"word": "tubulin", "variants": [{"spelling": "tublin", "offer": "same"}]},
            {"word": "hoeschst", "variants": []},
            {"word": "tax"},
        ]
    }

    assert rows_of_queue(served) == (
        WordsRow("tubulin", ("tublin",)),
        WordsRow("hoeschst"),
        WordsRow("tax"),
    )
    assert rows_of_queue({}) == ()


def test_the_dashboard_s_own_queue_is_read_as_rows_with_the_spellings_it_offers(
    project: Project,
) -> None:
    rows = words_queue_rows(project.path)

    assert rows[0] == WordsRow("tubulin", ("tublin",))
    # Every domain section loads, so the queue holds no pack row, and the slip hoeschst is
    # read as hoechst: the first three are the unsure words.
    assert [row.state for row in rows[:3]] == ["unknown", "variant", "unknown"]
    assert all(row.state != "pack" for row in rows)
    assert WordsRow("tublin", ("tubulin",)) in rows
    assert WordsRow("hoechst") not in rows and WordsRow("hoeschst") not in rows
    assert PAGE_STATES == ("unknown", "contested", "glued", "slip", "variant", "pack")
    without_packs = words_queue_rows(project.path, states=PAGE_STATES[:-1])
    assert without_packs == rows
    assert [row.word for row in words_rows(project.path)][:2] == ["tubulin", "tublin"]
    assert all(not row.spellings for row in words_rows(project.path))


def test_a_pack_row_uses_a_place_and_gets_no_answer(project: Project) -> None:
    queue = [WordsRow("hoechst", state="pack"), WordsRow("tubulin", ("tublin",))]

    first_place = teach_words(project.path, KEY, queue, 1)
    assert (first_place.looked_at, first_place.rows_answered, len(first_place.answered)) == (
        1,
        0,
        0,
    )
    assert first_place.skipped == {
        "a shipped pack binds it (the answer there is to accept the pack)": 1
    }
    assert not read_events(project.path)

    second_place = teach_words(project.path, KEY, queue, 2)
    assert [a.word for a in second_place.answered] == ["tubulin", "tublin"]
    assert (second_place.looked_at, second_place.rows_answered) == (2, 1)


def test_a_round_taught_from_the_dashboard_s_queue_answers_a_word_and_its_slip_together(
    project: Project,
) -> None:
    taught = teach_words(project.path, KEY, words_queue_rows(project.path), 2)

    # Row 1 is tubulin with tublin beside it. Row 2 (tublin) is hidden once tublin is answered, so
    # the second place is the next row. The slip hoeschst is read as hoechst and is no row, so the
    # next row is a word the key has no meaning for.
    assert [(a.word, a.event, a.same_as) for a in taught.answered] == [
        ("tubulin", "define", None),
        ("tublin", "assign", "tubulin"),
    ]
    assert (taught.looked_at, taught.rows_answered) == (2, 1)
    assert taught.to_json()["answered_same_thing"] == 1
    assert taught.skipped["already answered in this round"] == 1


def test_a_word_is_found_in_the_key_as_the_tokeniser_reads_it(project: Project) -> None:
    """The route serves words as the census stores them, but a spelling in any other clothes (a
    capital, a code's number, a separator) must still find its meaning."""
    key = AnswerKey(
        families={},
        words=WordsFamily(
            spellings={
                **SPELLINGS,
                "cos7": WordMeaning("COS7", "cos7", "subject", True, ("original",)),
            }
        ),
    )
    queue = [WordsRow("TUBULIN", ("TubLin", "Tax-3", "Cos-7")), WordsRow("tax")]

    taught = teach_words(project.path, key, queue, 1)

    # Capitals: TUBULIN and TubLin; a code: Tax-3 reads as its letters; a separator: Cos-7 reads as
    # the word the key's cos7 reads as, which the Dictionary places.
    assert [(a.word, a.term, a.same_as) for a in taught.answered] == [
        ("tubulin", "tubulin", None),
        ("tublin", "tubulin", "tubulin"),
        ("tax", "taxol", None),
    ]
    assert taught.skipped == {"the Dictionary already places it": 1}
    assert [e.target for e in read_events(project.path)] == [
        "term:tubulin",
        "term:tublin",
        "term:tax",
    ]


def test_a_spelling_a_capital_divides_is_split_as_the_page_writes_it(project: Project) -> None:
    row = WordsRow("tubhoe", glued="TubHoe")

    taught = teach_words(project.path, KEY, [row], 1, pairs="split")

    [event] = read_events(project.path)
    assert (event.kind, event.actor, event.target) == (
        RecordEventKind.ASSIGN,
        APPROVER,
        "split:tubhoe",
    )
    payload = json.loads(event.payload)
    assert (payload["reading"], payload["answer"], payload["written"]) == (
        "pieces",
        "separate words",
        "TubHoe",
    )
    assert [(a.word, a.event, a.term) for a in taught.answered] == [("tubhoe", "split", "")]
    assert taught.to_json()["answered_by_event"] == {"split": 1}
    # The engine reads it back as the page's own answer.
    assert project_answers(project.path) == {"tubhoe": Reading.PIECES}


def test_the_oracle_writes_the_split_the_page_writes(tmp_path: Path) -> None:
    from fixtures.words_pool import build_words_project
    from ingence.api.routes import decide

    base = build_words_project(tmp_path / "pool", infer=False)
    by_oracle, by_route = tmp_path / "oracle", tmp_path / "route"
    shutil.copytree(base, by_oracle)
    shutil.copytree(base, by_route)
    key = AnswerKey(
        families={},
        words=WordsFamily(
            spellings={
                "zibkelt": WordMeaning(
                    "zib Kelt",
                    "zib_kelt",
                    "subject",
                    False,
                    ("glued",),
                    (("zib", "subject"), ("kelt", "subject")),
                )
            }
        ),
    )

    taught = teach_words(by_oracle, key, [WordsRow("zibkelt", glued="zibKelt")], 1, pairs="split")
    with open_catalog(by_route) as catalog:
        context = decide.DecisionContext(catalog, by_route)
        decide._decide_split(
            context,
            "zibKelt",
            decide.Decision(target="split:zibKelt", word="pieces"),
            actor=APPROVER,
            occurred_at="2026-01-01T01:00:00Z",
            source={"kind": "the Words page"},
        )

    [ours] = read_events(by_oracle)
    [theirs] = read_events(by_route)
    assert [(a.word, a.event) for a in taught.answered] == [("zibkelt", "split")]
    assert (ours.kind, ours.target, ours.scope, ours.actor) == (
        theirs.kind,
        theirs.target,
        theirs.scope,
        theirs.actor,
    )
    ours_payload, theirs_payload = json.loads(ours.payload), json.loads(theirs.payload)
    for field in ("reading", "answer", "written"):
        assert ours_payload[field] == theirs_payload[field], field
    assert (ours.target, ours_payload["written"]) == ("split:zibkelt", "zibKelt")


def test_a_name_the_page_offers_no_way_to_divide_is_left_alone_under_split(
    project: Project,
) -> None:
    taught = teach_words(project.path, KEY, [WordsRow("tubhoe")], 1, pairs="split")

    assert taught.skipped == {"the page offers no way to divide it": 1}
    assert not read_events(project.path)
    # Under the other two readings the same row is a sense: of its own, or its first word's.
    own = teach_words(project.path, KEY, [WordsRow("tubhoe", glued="TubHoe")], 1)
    assert [(a.event, a.term) for a in own.answered] == [("define", "tubulin_hoechst")]


def test_the_words_the_page_serves_keep_the_states_asked_for_and_the_glued_spelling() -> None:
    served = {
        "rows": [
            {"word": "dmso", "state": "pack", "variants": []},
            {"word": "tubulin", "state": "unknown", "variants": [{"spelling": "tublin"}]},
            {
                "word": "zibkelt",
                "state": "glued",
                "variants": [],
                "glued": {"written": "zibKelt", "pieces": ["zib", "Kelt"]},
            },
        ]
    }

    assert rows_of_queue(served, states=("unknown", "glued")) == (
        WordsRow("tubulin", ("tublin",)),
        WordsRow("zibkelt", glued="zibKelt"),
    )
    assert [row.word for row in rows_of_queue(served)] == ["dmso", "tubulin", "zibkelt"]


def test_the_words_answered_split_into_spellings_a_rewrite_made_and_original_words() -> None:
    answers = (
        WordAnswer("ctrl", "assign", "control", "condition", ("case", "original")),
        WordAnswer("noc", "define", "nocodazole", "reagent", ("code",)),
        WordAnswer("hela", "assign", "hela", "subject", ("original",)),
        WordAnswer("hoecst", "assign", "nuclear_stain", "reagent", ("slip", "original")),
    )

    shown = Teaching(10, 4, 4, answers, {}).to_json()

    # No word is in both: a word the word list writes (its case apart) is an original word.
    assert shown["answered_spellings_made_by_a_rewrite"] == 2
    assert shown["answered_original_words"] == 2
    assert (
        shown["answered_spellings_made_by_a_rewrite"] + shown["answered_original_words"]
        == (shown["answered"])
    )


def test_a_measurement_runs_each_k_by_the_same_path_and_the_columns_differ_by_the_answers(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path / "pool")

    report = measure_teaching(KEY, pool, tmp_path / "work", [3, 0])

    assert list(report["columns"]) == ["k=0", "k=3"]
    zero, three = report["columns"]["k=0"], report["columns"]["k=3"]
    # The slip hoeschst is read as hoechst before any teaching, so two words are taught.
    assert (zero["taught"]["answered"], three["taught"]["answered"]) == (0, 2)
    assert zero["reading"]["words"]["read"] == three["reading"]["words"]["read"]
    assert zero["reading"]["words"]["unknown"] - three["reading"]["words"]["unknown"] == 5
    assert zero["families"] == three["families"]
    assert report["queue"]["first"][:2] == ["tubulin", "tublin"]
    # The default queue is the Dashboard's own: it offers a spelling beside a word.
    assert report["queue"]["rows"] >= 5 and report["queue"]["spellings_beside"] >= 2
    assert report["queue"]["first_rows"][0] == {"word": "tubulin", "spellings": ["tublin"]}
    # The rows by state are in the report (there is no pack row).
    assert report["queue"]["by_state"]["unknown"] >= 1 and "pack" not in report["queue"]["by_state"]
    assert report["method"]["teaching"]["queue"] == "words_queue_rows"
    assert report["method"]["groups"] == {"words_as": ["terms"]}
    assert zero["groups_words_as"] == three["groups_words_as"] == "terms"
    assert set(report["method"]["figures"]) >= {"teaching", "reading", "themes.ami_placed"}
    assert report["method"]["teaching"]["pair_answers"] == "own"
    assert report["method"]["teaching"]["k_counts"] == "rows looked at"
    text = taught_summary(report)
    assert "rows looked at" in text and "How each figure is made:" in text


def test_a_tier_off_arm_is_measured_beside_the_tier_on_arm(tmp_path: Path) -> None:
    pool = _pool(tmp_path / "pool")
    originals = AnswerKey(
        families={},
        words=WordsFamily(
            spellings={
                spelling: meaning
                for spelling, meaning in SPELLINGS.items()
                if meaning.shapes == ("original",)
            }
        ),
    )

    report = measure_teaching_arms(
        {"": (KEY, pool), "tier off ": (originals, pool)}, tmp_path / "work", [2]
    )

    assert list(report["columns"]) == ["k=2", "tier off k=2"]
    assert list(report["arms"]) == ["main", "tier off"]
    # The tier-on key teaches the slip beside its word in the first row; the originals' key cannot
    # teach the slip. hoeschst is read as hoechst, so no second slip is taught.
    assert [report["columns"][name]["taught"]["answered"] for name in report["columns"]] == [2, 1]
    assert report["columns"]["tier off k=2"]["taught"]["answered_words"] == ["tubulin"]
    assert "tier off k=2" in taught_summary(report)
    assert "== tier off k=2" in summary(report)


def test_a_measurement_is_resumable_column_by_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.evaluation import score

    pool = _pool(tmp_path / "pool")
    work, saved = tmp_path / "work", tmp_path / "saved"

    first = measure_teaching(KEY, pool, work, [0, 3], save=saved, keep_projects=False)

    assert sorted(path.name for path in saved.glob("*.json")) == ["k0.json", "k3.json"]
    assert not list(work.glob("column-*")), "a column's project is removed once its file is written"

    def without_seconds(columns: dict) -> dict:
        return {
            name: {key: value for key, value in column.items() if key != "seconds"}
            for name, column in columns.items()
        }

    # A column lost is made again from the indexed project, which is reused, and equals the first.
    (saved / "k3.json").unlink()
    monkeypatch.setattr(score, "_index_and_dive", lambda *a, **k: pytest.fail("index again"))
    again = measure_teaching(KEY, pool, work, [0, 3], save=saved, keep_projects=False)
    assert without_seconds(again["columns"]) == without_seconds(first["columns"])
    assert again["queue"] == first["queue"]

    # With every column saved, neither the index nor the queue is asked for.
    shutil.rmtree(work / "indexed")
    done = measure_teaching(KEY, pool, work, [0, 3], save=saved, keep_projects=False)
    assert done["columns"] == again["columns"]


def test_a_measurement_can_run_under_both_settings_of_the_lattice_s_words(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path / "pool")

    report = measure_teaching(KEY, pool, tmp_path / "work", [0], words_as=("terms", "spellings"))

    assert list(report["columns"]) == ["terms k=0", "spellings k=0"]
    # Each run says, in its own journal, which words it made its groups of.
    assert [report["columns"][name]["groups_words_as"] for name in report["columns"]] == [
        "terms",
        "spellings",
    ]
    assert report["method"]["groups"] == {"words_as": ["terms", "spellings"]}
    assert "groups.words_as" in taught_summary(report)
    with pytest.raises(ValueError, match="words_as is one of"):
        measure_teaching(KEY, pool, tmp_path / "other", [0], words_as=("both",))


def test_the_module_command_names_the_measurement(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["taught", "--help"])

    assert stopped.value.code == 0
    shown = capsys.readouterr().out
    for option in ("--vocabulary", "--k", "--pairs", "--with-tier-off", "--words-as", "--queue"):
        assert option in shown
    assert "default 0.5" in shown


def test_the_measurement_refuses_a_vocabulary_share_that_gives_it_nothing_to_teach(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for share in ("0", "0.0", "-0.2", "1.5"):
        code = main(["taught", str(tmp_path / f"never{share}"), "--vocabulary", share])

        assert code == 2
        assert "refused" in capsys.readouterr().err
        assert not (tmp_path / f"never{share}").exists()


def test_the_module_command_writes_the_tier_when_asked(tmp_path: Path) -> None:
    code = main(
        ["sample-lab", str(tmp_path / "cli"), "--tier", "S", "--seed", "0", "--vocabulary", "0.5"]
    )

    assert code == 0
    key = load_key(tmp_path / "cli" / "answer_key.json")
    assert key.words is not None
    assert key.describes["vocabulary"]["fraction"] == 0.5
