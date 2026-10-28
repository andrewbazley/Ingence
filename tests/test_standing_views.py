"""The standing views' pure functions: the Questions page's plain words, No analysis found's header
and thresholds, What changed's order and lines.

The three scripts are loaded in Node's ``vm`` with nothing of the page around them (each registers
its view only where ``VIEWS`` exists) and their functions are called on invented rows. Skipped where
Node is missing, never failed. Every control they name has words in the help catalog, read without
Node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "ingence" / "web"
SCRIPTS = ("questions.js", "found.js", "manifest.js")

#: Loads the three scripts into one context and answers each call read from stdin.
RUNNER = """
const vm = require('vm');
const fs = require('fs');
const context = vm.createContext({console});
for (const file of JSON.parse(process.argv[1])) {
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
}
const calls = JSON.parse(fs.readFileSync(0, 'utf8'));
const answers = calls.map(([object, name, args]) => context[object][name](...args));
const isMap = (v) => Object.prototype.toString.call(v) === '[object Map]';
process.stdout.write(JSON.stringify(answers, (key, value) => (isMap(value) ? [...value] : value)));
"""


def _call(*calls: tuple[str, str, list[Any]]) -> list[Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed here")
    files = json.dumps([str(WEB / name) for name in SCRIPTS])
    done = subprocess.run(
        [node, "-e", RUNNER, files],
        input=json.dumps(calls),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


def test_the_heading_says_how_many_questions_remain_and_how_many_lead() -> None:
    said = _call(
        ("questionsPage", "standsWords", ["Run 7", 41, 4]),
        ("questionsPage", "standsWords", ["Run 7", 1, 1]),
        ("questionsPage", "standsWords", ["Run 19", 0, 0]),
        ("questionsPage", "leadHeadWords", [4]),
    )
    assert said == [
        "Run 7 stands as read. 41 questions remain; 4 of them lead.",
        "Run 7 stands as read. 1 question remains; 1 of them leads.",
        "Run 19 stands as read. 0 questions remain; 0 of them lead.",
        "4 lead: a rule asks them first, or a grouping waits on them",
    ]


def test_the_questions_still_to_answer_fall_as_answers_are_given_on_the_page() -> None:
    summary = {"questions": 41, "answered": 2, "leading": 4, "leading_answered": 1}
    (said,) = _call(
        (
            "questionsPage",
            "remaining",
            [summary, [True, False, True]],
        )
    )
    # Two were answered when the page was read; three more since, two of them leaders.
    assert said == {"remain": 36, "lead": 1}


def test_what_an_answer_settles_is_said_by_kind_in_the_route_s_unit() -> None:
    word = {"kind": "word", "settles_n": 38, "settles_unit": "files", "classes_touched": 1}
    pack = {
        "kind": "pack",
        "settles_n": 64,
        "settles_unit": "files",
        "tops": list("abc"),
        "examples": ["a/x.csv"],
    }
    idle = {
        "kind": "pack",
        "settles_n": 1,
        "settles_unit": "files",
        "tops": ["(top level)"],
        "examples": [],
        "asked_because": "the clinical pack reads nothing here: no name binds its 18 meanings",
    }
    audit = {"kind": "audit", "settles_n": 198, "settles_unit": "conclusions", "classes_touched": 0}
    pair = {"kind": "atypical pair", "settles_n": 1, "settles_unit": "pairs"}
    said = _call(
        ("questionsPage", "settlesSentence", [word]),
        ("questionsPage", "settlesSentence", [pack]),
        ("questionsPage", "settlesSentence", [idle]),
        ("questionsPage", "settlesSentence", [audit]),
        ("questionsPage", "settlesSentence", [pair]),
        ("questionsPage", "settlesCell", [word]),
        ("questionsPage", "settlesCell", [audit]),
        ("questionsPage", "settlesCell", [pair]),
    )
    assert said[0] == {
        "lead": "Answering settles ",
        "strong": "38 files",
        "tail": (
            " whose names hold the word; 1 grouping waits on it. The answer applies to this root."
        ),
    }
    assert said[1] == {
        "lead": "It would read ",
        "strong": "64 files",
        "tail": " in 3 folders. Three it would read, as examples:",
    }
    # A pack that reads nothing is asked about in the rule's own words, never as a file count.
    assert said[2] == {
        "lead": "",
        "strong": "",
        "tail": "The clinical pack reads nothing here: no name binds its 18 meanings.",
    }
    assert said[3]["strong"] == "198 conclusions"
    assert said[4]["strong"] == "1 pair"
    assert said[5:] == ["38 files \u00b7 1 grouping", "198 conclusions", "1 pair"]
    # The route's word is "conclusions" or "pairs": "claims" never reaches a card.
    assert all("claim" not in json.dumps(item) for item in said)


def test_a_card_names_a_tied_word_s_meanings_from_the_route_and_never_splits_a_sentence() -> None:
    senses = [
        {"term": "scan", "theme_kind": "technique"},
        {"term": "scanner", "theme_kind": "instrument"},
    ]
    three = [*senses, {"term": "lag_time", "theme_kind": None}]
    said = _call(
        (
            "questionsPage",
            "cardTitle",
            [{"kind": "word", "question": 'What does "scan" mean here?', "senses": senses}],
        ),
        (
            "questionsPage",
            "cardTitle",
            [{"kind": "word", "question": 'What does "scan" mean here?', "senses": three}],
        ),
        (
            "questionsPage",
            "cardTitle",
            [{"kind": "pack", "question": "Keep the histology pack?", "senses": None}],
        ),
        ("questionsPage", "answerText", [{"senses": senses}, {"word": "scan", "label": "scan"}]),
        (
            "questionsPage",
            "answerText",
            [{"senses": senses}, {"word": "cannot_say", "label": "Can't say"}],
        ),
    )
    assert said[0] == 'What does "scan" mean here: scan or scanner?'
    assert said[1] == 'What does "scan" mean here: scan, scanner or lag time?'
    assert said[2] == "Keep the histology pack?"
    # An answer says the meaning's kind beside it, as the card's box does.
    assert said[3:] == ["scan \u00b7 technique", "Can't say"]


def test_examples_are_counted_in_words_and_a_machine_s_path_is_never_shown_whole() -> None:
    said = _call(
        ("questionsPage", "examplesCaption", [{"examples": ["a", "b", "c"], "examples_of": 38}]),
        ("questionsPage", "examplesCaption", [{"examples": ["a"], "examples_of": 1}]),
        ("questionsPage", "relativePath", ["plates/2024/hela_dapi_03.tif"]),
        ("questionsPage", "relativePath", ["/Users/someone/data/a.csv"]),
        ("questionsPage", "relativePath", ["C:\\data\\a.csv"]),
    )
    assert said == [
        "three of 38, relative to the data folder",
        "one of 1, relative to the data folder",
        "plates/2024/hela_dapi_03.tif",
        "a.csv",
        "a.csv",
    ]


def test_an_answer_is_dated_as_the_card_says_it() -> None:
    (said,) = _call(("questionsPage", "whenWords", ["2026-10-13T13:12:00"]))
    assert said == "13 Oct 1:12 PM"
    (nothing,) = _call(("questionsPage", "whenWords", [None]))
    assert nothing == ""


def test_the_chips_are_the_titles_with_questions_that_do_not_lead() -> None:
    titles = [
        {"title": "Vocabulary packs", "questions": 4, "leading": 4, "answered": 0, "first_rank": 1},
        {
            "title": "Words and their meanings",
            "questions": 3,
            "leading": 1,
            "answered": 0,
            "first_rank": 5,
        },
        {
            "title": "Conclusions to check",
            "questions": 29,
            "leading": 0,
            "answered": 0,
            "first_rank": 9,
        },
    ]
    (said,) = _call(("questionsPage", "restChips", [titles]))
    # A title whose every question leads has no chip; the others count what is left of them.
    assert said == [
        {"title": "Words and their meanings", "count": 2},
        {"title": "Conclusions to check", "count": 29},
    ]


def _row(reason: str | None) -> dict[str, Any]:
    return {"reason": reason, "folder": "vorn/2021-04-02 kelt brisa"}


def test_no_analysis_found_says_listed_of_judged_and_its_two_reasons() -> None:
    settings = {
        "judged_from_acquisitions": 5,
        "listed_below_share": 0.5,
        "own_analysis_files": 5,
        "own_result_tables": 3,
        "forgotten_at_least": 0.5,
        "stopped_below": 0.2,
    }
    found = {
        "listed": [_row("forgotten"), _row("stopped"), _row("stopped"), _row(None)],
        "judged": 32,
        "themes_computed": True,
        "settings": settings,
    }
    unthemed = {**found, "themes_computed": False, "themes_said": "Themes not computed: no reader."}
    nothing = {"listed": [], "judged": 0, "themes_computed": True, "settings": settings}
    said = _call(
        ("standingFound", "headerWords", [found]),
        ("standingFound", "headerWords", [unthemed]),
        ("standingFound", "headerWords", [nothing]),
        ("standingFound", "groupedRows", [found]),
        ("standingFound", "groupedRows", [unthemed]),
        ("standingFound", "thresholdParts", [settings]),
    )
    assert said[0] == [
        "4 of 32 experiments with raw data: no analysis found in the files Ingence read.",
        "1 looks forgotten: the rest of its theme is mostly analyzed. 2 belong to a theme "
        "that stopped. 1 has no reason given.",
    ]
    assert said[1][1] == "Themes not computed: no reader."
    assert "5 raw acquisitions" in said[2][0]
    assert [(group["heading"], len(group["rows"])) for group in said[3]] == [
        ("Forgotten", 1),
        ("A theme that stopped", 2),
        ("No reason given", 1),
    ]
    # Without themes, nothing is grouped by reason.
    assert [(group["heading"], len(group["rows"])) for group in said[4]] == [("", 4)]
    names = [name for _, name in said[5]]
    assert names == [f"completeness.{name}" for name in settings]
    assert "below 50%" in said[5][1][0]


def test_the_engine_s_label_is_spelled_the_american_way_and_a_folder_named_by_its_end() -> None:
    said = _call(
        ("standingFound", "american", ["Analysed elsewhere"]),
        ("standingFound", "american", ["no analysis found"]),
        ("standingFound", "folderName", ["vorn/2021-04-02 kelt brisa"]),
        (
            "standingFound",
            "answeredWords",
            [
                {
                    "analysed_elsewhere": {
                        "said_now": True,
                        "read_by_run": False,
                        "actor": "a. tester",
                    }
                },
                [{"word": "elsewhere", "label": "Analysed elsewhere"}],
            ],
        ),
    )
    assert said[:3] == ["Analyzed elsewhere", "no analysis found", "2021-04-02 kelt brisa"]
    assert said[3] == {
        "waiting": True,
        "text": "Analyzed elsewhere · waits for Re-run · a. tester",
    }


def test_what_changed_puts_the_newest_run_first_and_says_a_decision_in_a_line() -> None:
    runs = [
        {"run_id": "a", "number": 1},
        {"run_id": "c", "number": 3},
        {"run_id": "b", "number": 2},
    ]
    decision = {
        "actor": "a. tester",
        "occurred_at": "2026-10-09T04:48:13Z",
        "place": "vorn/2021-04-02 kelt brisa",
        "answer": "analyzed elsewhere",
    }
    said = _call(
        ("standingManifest", "newestFirst", [runs]),
        ("standingManifest", "decisionLine", [decision]),
    )
    assert [run["run_id"] for run in said[0]] == ["c", "b", "a"]
    assert said[1] == (
        "a. tester, 2026-10-09 04:48 UTC: vorn/2021-04-02 kelt brisa — analyzed elsewhere."
    )


def test_every_control_the_standing_views_name_has_words_in_the_help_catalog() -> None:
    catalog = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))
    keys: set[str] = set()
    for name in SCRIPTS:
        text = (WEB / name).read_text(encoding="utf-8")
        keys |= set(re.findall(r"'data-help': '([\w.]+)'", text))
        # The two answer words of No analysis found name their keys by the word.
        keys |= {f"found.{word}" for word in ("elsewhere", "not_elsewhere")}
    assert keys <= set(catalog["entries"]), keys - set(catalog["entries"])


def test_the_pages_never_say_never_analyzed_and_write_only_through_the_answer_route() -> None:
    for name in SCRIPTS:
        text = (WEB / name).read_text(encoding="utf-8")
        # What a page says: its string literals, never its comments.
        said = " ".join(re.findall(r"'[^'\n]*'|`[^`]*`", text)).lower()
        assert "never analyzed" not in said
        assert "never analysed" not in said
        assert set(re.findall(r"write\('([^']+)'", text)) <= {"/api/review/answer"}


def test_a_manifest_says_each_sentence_once_and_a_decision_s_place_in_words() -> None:
    decision = {
        "actor": "a. tester",
        "occurred_at": "2026-10-09T05:01:19Z",
        "target": "analysed_elsewhere:entry_zib",
        "place": "the analysis of tools/Vorn",
        "answer": "Analysed elsewhere",
        "files_changed": None,
        "said": "a. tester's answer took effect.",
    }
    audit = {
        **decision,
        "target": "class:a_rule:0123",
        "place": "every conclusion read as \u201cright\u201d by one rule",
    }
    traced = {**decision, "files_changed": 3, "files_beneath": 12, "itself_changed": True}
    manifest = {
        "said": [
            "a. tester's answer took effect.",
            "5 of 9 files now read as zib.",
            "Themes: one.",
        ],
        "decisions": [decision],
        "withdrawals": [],
        "themes": {"said": ["Themes: one.", "Themes: one."]},
    }
    said = _call(
        ("standingManifest", "decisionLine", [decision]),
        ("standingManifest", "plainPlace", [audit]),
        ("standingManifest", "tracedWords", [decision]),
        ("standingManifest", "tracedWords", [traced]),
        ("standingManifest", "changeSentences", [manifest]),
        ("standingManifest", "themeSentences", [manifest]),
    )
    # The engine says where a decision was about; the page shows its words.
    assert said[0] == (
        "a. tester, 2026-10-09 05:01 UTC: the analysis of tools/Vorn — Analyzed elsewhere."
    )
    assert said[1] == "every conclusion read as \u201cright\u201d by one rule"
    assert said[2] == "Ingence did not trace what it changed."
    assert said[3] == "3 of 12 files below it have new results. Its own results changed too."
    assert said[4] == ["5 of 9 files now read as zib."]
    assert said[5] == ["Themes: one."]


def test_a_quality_figure_is_said_as_a_count_in_its_whole_and_never_as_zero() -> None:
    counted = {
        "state": "counted",
        "count": 480,
        "total": 508,
        "seconds": None,
        "unit": "files",
        "was_state": "counted",
        "was_count": 462,
        "was_total": 508,
        "was_seconds": None,
    }
    whole_less = {**counted, "total": None, "count": 1200, "unit": "bytes"}
    timed = {**counted, "count": None, "total": None, "seconds": 125, "was_seconds": 58}
    not_run = {**counted, "state": "not_run", "count": None, "total": None, "was_state": "not_run"}
    not_kept = {**not_run, "state": "not_recorded"}
    first = {**counted, "was_state": None}
    rows = [
        {**counted, "section": "read"},
        {**not_run, "section": "conclusions", "figure": "a"},
        {**not_kept, "section": "cost", "figure": "b"},
    ]
    said = _call(
        ("standingManifest", "qualityValue", [counted]),
        ("standingManifest", "qualityValue", [counted, "was"]),
        ("standingManifest", "qualityValue", [whole_less]),
        ("standingManifest", "qualityValue", [timed]),
        ("standingManifest", "qualityValue", [timed, "was"]),
        ("standingManifest", "qualityValue", [not_run]),
        ("standingManifest", "qualityValue", [not_kept]),
        ("standingManifest", "qualityValue", [first, "was"]),
        ("standingManifest", "qualityGroups", [{"rows": rows}]),
        ("standingManifest", "seconds", [3725]),
    )
    assert said[0] == "480 of 508 files"
    assert said[1] == "462 of 508 files"
    assert said[2] == "1,200 bytes"
    assert said[3] == "2 min 5 s"
    assert said[4] == "58 s"
    assert said[5] == "not measured in this scan"
    assert said[6] == "not recorded"
    assert said[7] == "no earlier value"
    # A figure not stated is set apart from the stated ones, once each, and is not in a table.
    assert [group["section"] for group in said[8]["sections"]] == ["read"]
    assert [row["figure"] for row in said[8]["unstated"]] == ["a", "b"]
    assert said[9] == "1 h 2 min"


def test_a_quality_change_is_never_a_signed_zero_and_a_missing_figure_gives_its_reason() -> None:
    base = {"changed": True, "state": "counted", "unit": "files", "seconds": None}
    only_the_whole = {**base, "change": 0, "total": 6, "was_total": 5}
    up = {**base, "change": 2, "total": 6, "was_total": 6}
    seconds_down = {**base, "change": -62, "seconds": 125}
    lost = {**base, "state": "not_recorded", "change": None}
    unchanged = {**base, "changed": False, "change": 0}
    reasons = [
        {"state": "not_run", "note": ""},
        {"state": "not_recorded", "note": "the summary core failed: KeyError"},
        {"state": "not_recorded", "note": ""},
    ]
    said = _call(
        *(
            ("standingManifest", "qualityChange", [row])
            for row in (only_the_whole, up, seconds_down, lost, unchanged)
        ),
        *(("standingManifest", "qualityReason", [row]) for row in reasons),
    )
    assert said[:5] == ["whole 5 to 6", "+2", "-1 min 2 s", "no longer stated", ""]
    assert said[5:] == [
        "not measured in this scan",
        "not recorded (the summary core failed: KeyError)",
        "not recorded",
    ]
