"""The Overview's and Checks' pure words: overview.js in Node's ``vm``.

The script registers its views only where ``VIEWS`` exists, so it loads here with nothing of the
page around it, and its pure functions are called on invented values. Skipped where Node is
missing, never failed. The help keys it names are read without Node.
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
SCRIPT = WEB / "overview.js"
CATALOG = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))

RUNNER = """
const vm = require('vm');
const fs = require('fs');
const context = vm.createContext({console});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const calls = JSON.parse(fs.readFileSync(0, 'utf8'));
const page = context.overviewPage;
process.stdout.write(JSON.stringify(calls.map(([name, args]) => page[name](...args))));
"""


def _call(*calls: tuple[str, list[Any]]) -> list[Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed here")
    done = subprocess.run(
        [node, "-e", RUNNER, str(SCRIPT)],
        input=json.dumps(calls),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    answers: list[Any] = json.loads(done.stdout)
    return answers


def _overview(**changes: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "counts": {"experiments": 12, "files": 3400, "areas": 3},
        "date_range": {"first": "2022-02-03", "last": "2023-11-20", "basis": "names"},
        "file_types": [
            {"label": "TIFF image", "share": 0.991},
            {"label": "CSV table", "share": 0.009},
        ],
    }
    found.update(changes)
    return found


def test_the_sentence_says_the_counts_the_span_and_the_main_file_types() -> None:
    one = _overview(counts={"experiments": 1, "files": 1, "areas": 1})
    undated = _overview(date_range={"first": None, "last": None, "basis": "none"}, file_types=[])
    said = _call(
        ("overviewSentence", [_overview()]),
        ("overviewSentence", [one]),
        ("overviewSentence", [undated]),
        ("shareWords", [0.004]),
        ("typePlural", ["Spreadsheets"]),
    )
    assert said[0] == (
        "12 experiments in 3 areas, 2022-02 to 2023-11, 3,400 files "
        "(TIFF images 99%, CSV tables 1%)."
    )
    assert said[1].startswith("1 experiment in 1 area, 2022-02 to 2023-11, 1 file (")
    assert said[2] == "12 experiments in 3 areas, 3,400 files."
    assert said[3] == "less than 1%"
    assert said[4] == "Spreadsheets"


def test_a_goals_line_keeps_each_tier_apart_in_the_briefs_words() -> None:
    summary = {
        "experiments": 12,
        "named": 8,
        "associated": 3,
        "same_theme": 1,
        "first": "2023-03-02",
        "last": "2025-01-15",
        "raw_with_analysis": 7,
        "of": 12,
        "repeated_groups": 3,
        "seen_once": 4,
    }
    goal = {
        "matches": [
            {"experiment_id": "e3", "tier": "same_theme"},
            {"experiment_id": "e1", "tier": "named"},
            {"experiment_id": "e2", "tier": "named"},
        ]
    }
    line, tiers = _call(("goalLine", [summary]), ("tiersOf", [goal]))
    assert line == (
        "12 experiments relate (8 named, 3 associated, 1 same theme). 2023-03 to 2025-01. "
        "Raw to analysis: 7 of 12. Repeated: 3 groups; seen once: 4."
    )
    assert [tier["tier"] for tier in tiers] == ["named", "same_theme"]
    assert [match["experiment_id"] for match in tiers[0]["matches"]] == ["e1", "e2"]
    assert tiers[1]["words"] == "Same theme"


def test_the_parts_say_measured_counts_and_never_a_time_left() -> None:
    running = {
        "phase_label": "Reading the names",
        "step": 2,
        "steps": 3,
        "done": 120,
        "total": 400,
        "seconds_left": 99,
    }
    said = _call(
        ("analyzedWords", [{"raw_with_analysis": 410, "raw_files": 980}]),
        (
            "experimentsWords",
            [
                {
                    "experiments_complete": 9,
                    "experiments_partial": 4,
                    "experiments_raw_only": 3,
                    "experiments_analysis_only": 1,
                }
            ],
        ),
        ("repeatsWords", [{"seen_once": 1, "repeated": 6, "groups": 2}]),
        ("jobWords", [running]),
        ("jobWords", [{"phase_label": "Applying the rules", "steps": 0}]),
    )
    assert said[0] == "Raw files analyzed: 410 of 980"
    assert said[1] == "Experiments: 9 complete, 4 partial, 3 raw only, 1 analysis only."
    assert said[2] == "Seen once: 1 experiment. Repeated: 6 experiments in 2 groups."
    assert said[3] == "Reading the names: step 2 of 3, 120 of 400."
    assert said[4] == "Applying the rules."
    assert "99" not in said[3]


def test_a_write_says_what_the_engine_answered_and_an_answer_names_its_question() -> None:
    check = {
        "question_id": "q_plate_a",
        "answer": {"route": "/api/review/answer", "body": {"question_id": "q_plate_a"}},
    }
    said = _call(
        ("waitsWords", [{"recorded": False, "said": "Nothing was recorded."}]),
        ("waitsWords", [{"recorded": True, "waits_for_rerun": True}]),
        ("answerBody", [check, {"value": "yes"}, "alice"]),
        ("projectKey", ["/data/Example_Lab/project"]),
        ("projectKey", ["/data/Example_Lab/other"]),
    )
    assert said[0] == "Nothing was recorded."
    assert said[1] == "Waits for Re-run"
    assert said[2] == {
        "question_id": "q_plate_a",
        "actor": "alice",
        "target": "q_plate_a",
        "word": "yes",
    }
    assert re.fullmatch(r"[0-9a-f]{8}", said[3]) and said[3] != said[4]


#: The four keys that the tour points at.
TOUR_KEYS = {"overview.found", "overview.ask", "overview.completeness", "overview.assumptions"}


def test_every_help_key_the_script_names_has_words_in_the_catalog() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    keys = set(re.findall(r"'((?:overview|checks|assumptions|ask|workspace)\.[a-z_]+)'", source))
    assert {"overview.show_these", "assumptions.wrong", "checks.skip", "checks.keep"} <= keys
    assert keys >= TOUR_KEYS, "the tour's four keys are on the Overview's parts"
    assert keys <= set(CATALOG["entries"]), keys - set(CATALOG["entries"])
    assert set(CATALOG["tours"]["first_visit"]) <= keys
