"""Ask's pure words: ask.js in Node's ``vm``.

The script registers its view only where ``VIEWS`` exists, so it loads here with nothing of the
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
SCRIPT = WEB / "ask.js"
CATALOG = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))

RUNNER = """
const vm = require('vm');
const fs = require('fs');
const context = vm.createContext({console});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const calls = JSON.parse(fs.readFileSync(0, 'utf8'));
const page = context.askPage;
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


def test_the_reading_says_each_part_that_has_words() -> None:
    full, kept_only, nothing = _call(
        (
            "readWords",
            [{"kept": ["DAPI", "fixed"], "dropped": ["which", "the"], "unknown": ["zorbit"]}],
        ),
        ("readWords", [{"kept": ["HeLa"], "dropped": [], "unknown": []}]),
        ("readWords", [{"kept": [], "dropped": ["the"], "unknown": []}]),
    )
    assert full == "Read: DAPI, fixed. Left out: which, the. Not known: zorbit."
    assert kept_only == "Read: HeLa."
    assert nothing == "Read: nothing. Left out: the."


def test_the_filters_say_only_what_the_question_names() -> None:
    every, years, none = _call(
        ("filterWords", [{"years": [2023, 2024], "types": ["TIFF image"], "areas": ["Scope_1"]}]),
        ("filterWords", [{"years": [2024], "types": None, "areas": None}]),
        ("filterWords", [{"years": None, "types": None, "areas": None}]),
    )
    assert every == "Years: 2023, 2024. File types: TIFF image. Areas: Scope_1."
    assert years == "Years: 2024."
    assert none == ""


def test_an_expansion_is_labelled_and_matched_words_are_grouped_by_how() -> None:
    expansion, lines = _call(
        (
            "expansionWords",
            [{"word": "nuclear stain", "to": ["DAPI", "Hoechst"], "how": "a class"}],
        ),
        (
            "matchedLines",
            [
                [
                    {"word": "ADP", "how": "names contain it (nucleotide)"},
                    {"word": "ATP", "how": "names contain it (nucleotide)"},
                    {"word": "rep2", "how": "a repeat of a named experiment"},
                ]
            ],
        ),
    )
    assert expansion == {"label": "nuclear stain: DAPI, Hoechst", "how": "a class"}
    assert lines == [
        "ADP, ATP: names contain it (nucleotide)",
        "rep2: a repeat of a named experiment",
    ]


def test_dates_and_raw_files_analyzed_keep_the_unknown_unknown() -> None:
    span, one, undated, share, no_raw = _call(
        ("datesWords", [{"first": "2024-03-12", "last": "2024-03-15"}]),
        ("datesWords", [{"first": "2024-03-12", "last": "2024-03-12"}]),
        ("datesWords", [{"first": None, "last": None}]),
        ("completenessWords", [0.3]),
        ("completenessWords", [None]),
    )
    assert span == "2024-03-12 to 2024-03-15"
    assert one == "2024-03-12"
    assert undated == "Undated"
    assert share == "Raw files analyzed: 30%"
    assert no_raw == "No raw files found"


def test_tiers_are_named_then_associated_and_suggestions_are_kept_apart() -> None:
    experiments = [
        {"experiment_id": "e_b", "label": "Plate_A titration", "score_tier": "associated"},
        {"experiment_id": "e_a", "label": "2024-03-15 HeLa DAPI", "score_tier": "named"},
        {"experiment_id": "e_c", "label": "2024-04-02 HeLa GFP", "score_tier": "same_theme"},
    ]
    answer = {
        "experiments": experiments,
        "same_theme": [
            {"experiment_id": "e_d", "via_theme": "hela imaging", "label": "Plate_B HeLa"},
            {"experiment_id": "e_a", "via_theme": "hela imaging"},
            {"experiment_id": "e_c", "via_theme": "hela imaging"},
        ],
    }
    tiers, suggestions, said = _call(
        ("tiersOf", [experiments]),
        ("suggestionsOf", [answer]),
        ("suggestionWords", [{"via_theme": "hela imaging"}]),
    )
    assert [(group["tier"], group["words"]) for group in tiers] == [
        ("named", "Named"),
        ("associated", "Associated"),
    ]
    assert [[e["experiment_id"] for e in group["experiments"]] for group in tiers] == [
        ["e_a"],
        ["e_b"],
    ]
    # The same-theme tier first, then the list; a match is never also a suggestion; each once.
    assert [item["experiment_id"] for item in suggestions] == ["e_c", "e_d"]
    # The route names a suggestion itself, so the page need not read the run for its name.
    assert suggestions[1]["label"] == "Plate_B HeLa"
    assert said == "Suggestion: shares the theme hela imaging."


def test_save_as_goal_says_the_engines_words_when_nothing_is_recorded() -> None:
    recorded, not_recorded = _call(
        ("goalSaid", [{"goal": {"id": "goal_1", "text": "HeLa"}, "recorded": True}]),
        ("goalSaid", [{"recorded": False, "said": "Nothing was recorded."}]),
    )
    assert recorded == "Saved as a goal. The Overview shows it."
    assert not_recorded == "Nothing was recorded."


def test_every_help_key_the_script_names_has_words_in_the_catalog() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    keys = set(re.findall(r"'data-help': '([\w.]+)'", source))
    assert {"ask.open", "ask.read", "ask.save_goal", "ask.same_theme", "ask.tier"} <= keys
    for key in sorted(keys):
        entry = CATALOG["entries"].get(key)
        assert entry, key
        assert entry["title"].strip() and entry["text"].strip().endswith("."), key
