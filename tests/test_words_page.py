"""The Words section of Questions: its pure functions, and what its source must and must not hold.

``words.js`` is loaded in Node's ``vm`` with nothing of the page around it (the page's parts run
only where ``VIEWS`` exists), and the functions that decide what the page says and sends are called
on invented rows: the state chips, the reading meter, the order with skipped words last, the one
quiet line other pages say, and above all the batch one word's card writes (``batchFor``).
Skipped where Node is missing, never failed. The source tests need no Node: every control the
script builds names a help entry, its styles name only ``words-`` classes, and nothing in it reaches
a route but the queue, a word's two reads and the one answer route.

Every name here is invented.
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
CATALOG = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))
SCRIPT = (WEB / "words.js").read_text(encoding="utf-8")
STYLES = (WEB / "words.css").read_text(encoding="utf-8")
PAGE = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
KEYS = (WEB / "keys.js").read_text(encoding="utf-8")
QUESTIONS = (WEB / "questions.js").read_text(encoding="utf-8")

#: What nothing the page says may name, built from parts so this file names none.
NAMES_OF_AN_ASSISTANT = (
    r"\b("
    + "|".join(["cl" + "aude", "assis" + "tant", "chat" + "gpt", "g" + "pt", "l" + "lm"])
    + r")\b"
)

#: What the page says of a withdrawal no run has read.
NO_RUN_READ = "No scan has read this answer, so withdrawing cancels it. Nothing waits for Re-run."

RUNNER = """
const vm = require('vm');
const fs = require('fs');
const context = vm.createContext({console});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const calls = JSON.parse(fs.readFileSync(0, 'utf8'));
const revive = (value) => {
  if (Array.isArray(value)) return value.map(revive);
  if (value && typeof value === 'object') {
    if (value.__map__) return new Map(value.__map__.map(([key, held]) => [key, revive(held)]));
    return Object.fromEntries(Object.entries(value).map(([key, held]) => [key, revive(held)]));
  }
  return value;
};
const answers = calls.map(([name, args]) => context.wordsPage[name](...args.map(revive)));
const isMap = (v) => Object.prototype.toString.call(v) === '[object Map]';
process.stdout.write(JSON.stringify(answers, (key, value) => (isMap(value) ? [...value] : value)));
"""


def _call(*calls: tuple[str, list[Any]]) -> list[Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed here")
    done = subprocess.run(
        [node, "-e", RUNNER, str(WEB / "words.js")],
        input=json.dumps(calls),
        capture_output=True,
        text=True,
        check=True,
    )
    answers: list[Any] = json.loads(done.stdout)
    return answers


def _sense(term: str, kind: str, **more: Any) -> dict[str, Any]:
    return {
        "term": term,
        "theme_kind": kind,
        "origin": "builtin",
        "section": "core",
        "scope": "project",
        "role": "in_force",
        "accepted": True,
        "pack": None,
        **more,
    }


def _variant(spelling: str, reason: str, **more: Any) -> dict[str, Any]:
    return {
        "spelling": spelling,
        "files": 4,
        "occurrences": 4,
        "reason": reason,
        "reason_said": "one letter apart",
        "distance": None,
        "known_as": None,
        "offer": "same",
        "distinct_target": f"distinct:{'+'.join(sorted([spelling.lower(), 'zorbex']))}",
        **more,
    }


def _row(word: str = "zorbex", state: str = "unknown", **more: Any) -> dict[str, Any]:
    return {
        "word": word,
        "examples": [word],
        "files": 10,
        "occurrences": 12,
        "experiments": 2,
        "state": state,
        "of": None,
        "senses": [],
        "variants": [],
        "glued": None,
        "where": [{"path": "plates/day_one", "files": 6}],
        "where_not_shown": 0,
        "unknown_parts": 12,
        "covered": 12,
        "answered": None,
        "waiting": False,
        "target": f"term:{word}",
        **more,
    }


def _batch(row: dict[str, Any], **spec: Any) -> dict[str, Any]:
    spec.setdefault("choice", None)
    spec.setdefault("variants", {"__map__": []})
    (found,) = _call(("batchFor", [{"row": row, **spec}]))
    result: dict[str, Any] = found
    return result


def _the_page_agrees(row: dict[str, Any], decisions: list[dict[str, Any]], **spec: Any) -> None:
    """Where Node is present, the page's own ``batchFor`` builds exactly these decisions. The tests
    that send a batch to the engine write its body in Python, so they run without Node; this ties
    that body to what the page builds wherever Node is there."""
    if shutil.which("node") is None:
        return
    assert _batch(row, **spec)["decisions"] == decisions


def _answers(**given: tuple[str, str]) -> dict[str, list[Any]]:
    return {
        "__map__": [[spelling, {"answer": a, "meaning": m}] for spelling, (a, m) in given.items()]
    }


# ------------------------------------------------------------------ the list and the meter


def test_each_state_wears_its_chip_in_words_and_a_slip_or_variant_names_its_word() -> None:
    said = _call(
        ("stateChip", [_row(state="unknown")]),
        ("stateChip", [_row(state="contested")]),
        ("stateChip", [_row(state="glued")]),
        ("stateChip", [_row("zorbec", state="slip", of="zorbex")]),
        ("stateChip", [_row("zorbexes", state="variant", of="zorbex")]),
        ("stateChip", [_row(state="understood")]),
    )
    assert [chip["text"] for chip in said] == [
        "unknown",
        "tied",
        "glued",
        "a typo of zorbex",
        "a variant of zorbex",
        "understood",
    ]
    assert all(chip["title"].endswith(".") for chip in said)


def test_the_meter_is_the_headers_counts_each_with_what_it_counts() -> None:
    queue = {
        "reading": {"words": {"read": 500, "understood": 310, "unknown": 160, "contested": 30}},
        "unknown_parts": 160,
    }
    (meter,) = _call(("meterOf", [queue, 45]))
    assert meter == {"read": 500, "now": 310, "would": 355, "covered": 45, "unknown": 160}
    (said,) = _call(("meterWords", [meter]))
    assert said == (
        "310 of 500 word parts understood now. "
        "If every word down to this one were answered: 355 of 500."
    )
    # A count with no row open reads as the figure now, never as a share.
    (flat,) = _call(("meterOf", [queue, 0]))
    assert flat["would"] == flat["now"] == 310


def test_the_running_sums_equal_the_served_cumulative_counts_until_a_word_is_skipped() -> None:
    rows = [
        _row("alpha", unknown_parts=30, covered=50),
        _row("beta", unknown_parts=20, covered=70),
        _row("gamma", unknown_parts=0, covered=70),
        _row("delta", unknown_parts=5, covered=75),
    ]
    queue = {"rows": rows}
    (base,) = _call(("coveredBefore", [queue]))
    assert base == 20
    (sums,) = _call(("coveredAlong", [rows, base]))
    assert sums == [row["covered"] for row in rows]
    # Skipping a word moves it to the end; the sums are the served per-row counts, added anew.
    (moved,) = _call(("orderRows", [rows, ["alpha"]]))
    assert [row["word"] for row in moved] == ["beta", "gamma", "delta", "alpha"]
    (after,) = _call(("coveredAlong", [moved, base]))
    assert after == [40, 40, 45, 75]
    # Skipped words go last in the order they were skipped, and a word not in the page is ignored.
    (twice,) = _call(("orderRows", [rows, ["gamma", "missing", "alpha"]]))
    assert [row["word"] for row in twice] == ["beta", "delta", "gamma", "alpha"]


def test_a_files_bar_is_drawn_against_the_first_rows_files() -> None:
    said = _call(
        ("barWidth", [40, 40]), ("barWidth", [10, 40]), ("barWidth", [1, 400]), ("barWidth", [5, 0])
    )
    assert said == [100, 25, 1, 0]


def test_the_quiet_line_is_the_headers_counts_and_says_nothing_where_there_is_nothing() -> None:
    rows = [_row(f"w{at}", covered=10 * (at + 1), unknown_parts=10) for at in range(3)]
    said = _call(
        ("quietLine", [{"rows": rows, "unknown_parts": 80}]),
        ("quietLine", [{"rows": rows[:1], "unknown_parts": 80}]),
        ("quietLine", [{"rows": [], "unknown_parts": 80}]),
        ("quietLine", [{"rows": rows, "unknown_parts": 0}]),
        ("quietLine", [None]),
        ("quietLine", [{"rows": rows, "unknown_parts": 80}, 2]),
    )
    assert said[0] == (
        "3 words cover 30 of the 80 word parts Ingence does not know. Add their meanings."
    )
    assert said[1].startswith("1 word covers 10 of the 80 word parts")
    assert said[2:5] == ["", "", ""]
    assert said[5].startswith("2 words cover 20 of the 80")


# ------------------------------------------------------------------ the batch


def test_a_meaning_the_dictionary_holds_is_one_assignment_on_the_words_target() -> None:
    row = _row()
    found = _batch(
        row, choice={"type": "sense", "term": "stain", "theme_kind": "reagent", "inForce": False}
    )
    assert found["problem"] == "" and found["confirmed"] is False
    assert found["decisions"] == [
        {"target": "term:zorbex", "answer": "stain", "theme_kind": "reagent"}
    ]


def test_a_word_the_dictionary_places_is_answered_as_a_correction() -> None:
    row = _row("hela", state="contested", senses=[_sense("cell_line", "organism", role="tied")])
    found = _batch(
        row,
        choice={"type": "sense", "term": "cell_line", "theme_kind": "organism", "inForce": False},
    )
    assert found["decisions"] == [
        {"target": "term:hela", "answer": "cell_line", "theme_kind": "organism", "correcting": True}
    ]


def test_an_own_meaning_is_a_definition_and_below_a_folder_a_correction_there() -> None:
    row = _row()
    everywhere = _batch(
        row,
        choice={"type": "own"},
        own={"meaning": " zorbex stain ", "kind": "reagent", "below": None},
    )
    assert everywhere["decisions"] == [
        {"target": "term:zorbex", "answer": "zorbex stain", "theme_kind": "reagent"}
    ]
    below = _batch(
        row,
        choice={"type": "own"},
        own={"meaning": "zorbex stain", "kind": "reagent", "below": "plates/day_one"},
    )
    assert below["decisions"] == [
        {
            "target": "term:zorbex",
            "answer": "zorbex stain",
            "theme_kind": "reagent",
            "correcting": True,
            "below": "plates/day_one",
        }
    ]
    for given in ({"meaning": "", "kind": "reagent"}, {"meaning": "x", "kind": ""}):
        refused = _batch(row, choice={"type": "own"}, own={"below": None, **given})
        assert refused["decisions"] == []
        assert refused["problem"] == "Write what it means and choose what kind of thing it is."


def test_the_word_and_every_spelling_answered_with_it_are_one_batch_in_order() -> None:
    row = _row(
        variants=[
            _variant("zorbec", "slip"),
            _variant("zorbexes", "plural"),
            _variant("zorbex2", "same_slot", offer="kind"),
            _variant("zorbey", "slip"),
        ]
    )
    found = _batch(
        row,
        choice={"type": "own"},
        own={"meaning": "zorbex stain", "kind": "reagent", "below": None},
        variants=_answers(
            zorbec=("same", "zorbec"),
            zorbex2=("kind", "zorbex two"),
            zorbey=("unrelated", "zorbey"),
        ),
        note="  from the plate notes ",
    )
    assert found["problem"] == ""
    assert found["decisions"] == [
        {
            "target": "term:zorbex",
            "answer": "zorbex stain",
            "theme_kind": "reagent",
            "note": "from the plate notes",
        },
        {"target": "term:zorbec", "answer": "zorbex stain", "theme_kind": "reagent"},
        {"target": "term:zorbex2", "answer": "zorbex two", "theme_kind": "reagent"},
        {"target": "distinct:zorbex+zorbey", "word": "unrelated"},
    ]


def test_a_spelling_left_unanswered_writes_nothing_and_of_one_kind_defaults_to_itself() -> None:
    row = _row(variants=[_variant("zorbec", "slip"), _variant("zorbexes", "plural")])
    found = _batch(
        row,
        choice={"type": "sense", "term": "stain", "theme_kind": "reagent", "inForce": False},
        variants=_answers(zorbexes=("kind", "")),
    )
    assert [d["target"] for d in found["decisions"]] == ["term:zorbex", "term:zorbexes"]
    assert found["decisions"][1]["answer"] == "zorbexes"


def test_the_same_thing_needs_a_meaning_to_name_and_says_so() -> None:
    row = _row(variants=[_variant("zorbec", "slip")])
    for answer in ("same", "kind"):
        found = _batch(row, variants=_answers(zorbec=(answer, "")))
        assert found["decisions"] == []
        assert found["problem"].startswith('Say what "zorbex" means first')
    # "Unrelated" needs no meaning: it is about the pair.
    found = _batch(row, variants=_answers(zorbec=("unrelated", "")))
    assert found["decisions"] == [{"target": "distinct:zorbec+zorbex", "word": "unrelated"}]


def test_a_known_word_is_never_written_to_and_its_meaning_can_be_the_words_own() -> None:
    row = _row(
        "dapy",
        state="slip",
        of="dapi",
        variants=[_variant("dapi", "slip", known_as="dapi", files=0)],
    )
    near = {"__map__": [["dapi", [_sense("dapi", "reagent")]]]}
    # "The same thing" with no meaning chosen gives the word the known spelling's meaning.
    found = _batch(row, variants=_answers(dapi=("same", "")), near=near)
    assert found["decisions"] == [
        {"target": "term:dapy", "answer": "dapi", "theme_kind": "reagent"}
    ]
    # With the same meaning chosen it adds nothing; with another it says why not.
    chosen = {"type": "sense", "term": "dapi", "theme_kind": "reagent", "inForce": False}
    again = _batch(row, choice=chosen, variants=_answers(dapi=("same", "")), near=near)
    assert [d["target"] for d in again["decisions"]] == ["term:dapy"]
    other = _batch(
        row,
        choice={"type": "sense", "term": "other", "theme_kind": "reagent", "inForce": False},
        variants=_answers(dapi=("same", "")),
        near=near,
    )
    assert other["decisions"] == [] and "already known as dapi" in other["problem"]
    # With no near meaning to take, the person is asked to choose.
    nothing = _batch(row, variants=_answers(dapi=("same", "")))
    assert nothing["decisions"] == [] and nothing["problem"].startswith('Choose what "dapy" means')


def test_not_a_word_carries_what_it_is_to_a_computer_and_cannot_say_is_a_note() -> None:
    row = _row("tif")
    found = _batch(row, choice={"type": "not_a_word", "reading": "extension"})
    assert found["decisions"] == [
        {"target": "term:tif", "word": "not_a_word", "reading": "extension"}
    ]
    unsaid = _batch(row, choice={"type": "not_a_word", "reading": ""})
    assert unsaid["decisions"] == [] and "what it is to a computer" in unsaid["problem"]
    said = _batch(row, choice={"type": "cannot_say"}, note="ask the postdoc")
    assert said["decisions"] == [{"target": "term:tif", "note": "Can't say. ask the postdoc"}]


def test_a_glued_spellings_reading_is_its_own_decision_on_its_target() -> None:
    glued = {
        "written": "zibKelt",
        "pieces": ["zib", "kelt"],
        "whole_files": 5,
        "alone_files": [1, 2],
        "reading": "whole",
        "tier": "words",
        "asked": True,
        "because": "x",
        "target": "split:zibkelt",
    }
    row = _row("zibkelt", state="glued", glued=glued)
    whole = _batch(row, split="whole")
    assert whole["decisions"] == [{"target": "split:zibkelt", "word": "whole"}]
    pieces = _batch(row, split="pieces")
    assert pieces["decisions"] == [{"target": "split:zibkelt", "word": "pieces"}]


def test_the_pieces_of_a_glued_spelling_are_not_asked_again_as_spellings() -> None:
    row = _row(
        "zibkelt",
        state="glued",
        variants=[
            _variant("kelt", "glued"),
            _variant("zib", "glued"),
            _variant("zibkeld", "slip"),
        ],
    )
    piece = _row("kelt", variants=[_variant("zibkelt", "glued_in"), _variant("keld", "slip")])
    (asked, of_piece) = _call(("askedVariants", [row]), ("askedVariants", [piece]))
    assert [variant["spelling"] for variant in asked] == ["zibkeld"]
    assert [variant["spelling"] for variant in of_piece] == ["keld"]


def test_a_glued_row_is_named_by_its_reading_but_its_meaning_is_said_on_the_word() -> None:
    glued = {
        "written": "zibKelt",
        "pieces": ["zib", "kelt"],
        "whole_files": 5,
        "alone_files": [1, 2],
        "reading": "whole",
        "tier": "words",
        "asked": True,
        "because": "x",
        "target": "split:zibkelt",
    }
    row = _row("zibkelt", state="glued", glued=glued, target="split:zibkelt")
    found = _batch(
        row,
        choice={"type": "own"},
        own={"meaning": "zib kelt mix", "kind": "reagent", "below": None},
        split="whole",
    )
    assert found["decisions"] == [
        {"target": "term:zibkelt", "answer": "zib kelt mix", "theme_kind": "reagent"},
        {"target": "split:zibkelt", "word": "whole"},
    ]


def test_a_spelling_answered_with_a_word_kept_below_a_folder_is_kept_below_it_too() -> None:
    row = _row(variants=[_variant("zorbec", "slip"), _variant("zorbexes", "plural")])
    found = _batch(
        row,
        choice={"type": "own"},
        own={"meaning": "zorbex stain", "kind": "reagent", "below": "plates/day_one"},
        variants=_answers(zorbec=("same", ""), zorbexes=("kind", "zorbex stains")),
    )
    place = {"correcting": True, "below": "plates/day_one"}
    assert found["decisions"] == [
        {
            "target": "term:zorbex",
            "answer": "zorbex stain",
            "theme_kind": "reagent",
            "correcting": True,
            "below": "plates/day_one",
        },
        {"target": "term:zorbec", "answer": "zorbex stain", "theme_kind": "reagent", **place},
        {"target": "term:zorbexes", "answer": "zorbex stains", "theme_kind": "reagent", **place},
    ]
    # A meaning for the whole project carries nothing of the kind to its spellings.
    whole = _batch(
        row,
        choice={"type": "own"},
        own={"meaning": "zorbex stain", "kind": "reagent", "below": None},
        variants=_answers(zorbec=("same", "")),
    )
    assert "below" not in whole["decisions"][1] and "correcting" not in whole["decisions"][1]


def test_the_top_level_is_not_a_folder_a_meaning_can_be_kept_below() -> None:
    row = _row(where=[{"path": "", "files": 3}, {"path": "plates/day_one", "files": 2}])
    (folders,) = _call(("foldersOf", [row]))
    assert folders == [{"path": "plates/day_one", "files": 2}]


def test_a_near_spellings_meaning_is_never_the_one_in_force_on_the_word() -> None:
    row = _row("dapy", state="understood", senses=[_sense("stain", "reagent")])
    near = {
        "__map__": [
            ["dapi", [{"term": "nuclear_stain", "theme_kind": "reagent", "origin": "core"}]]
        ]
    }
    (options,) = _call(("meaningOptions", [row, near]))
    assert [(o["term"], o["role"], o["from"]) for o in options] == [
        ("stain", "in_force", None),
        ("nuclear_stain", "near", "dapi"),
    ]


def test_every_origin_the_dictionary_names_is_said_in_plain_words() -> None:
    said = _call(
        *[
            (
                "optionLine",
                [{"origin": origin, "accepted": True, "role": "in_force", "from": None}, ""],
            )
            for origin in ("core", "domain", "laboratory", "yours", "builtin", "pack", "user")
        ]
    )
    assert said == [
        "the built-in words",
        "a domain word list",
        "your laboratory\u2019s word list",
        "your own answer",
        "the built-in words",
        "a domain word list",
        "your own answer",
    ]


def test_what_was_recorded_does_not_count_a_pair_called_unrelated_as_waiting() -> None:
    said = _call(
        ("recordedWords", ["zorbex", "Dana Whitfield", ["term:zorbex"]]),
        ("recordedWords", ["zorbex", "Dana Whitfield", ["term:zorbex", "term:zorbec"]]),
        ("recordedWords", ["zorbex", "Dana Whitfield", ["term:zorbex", "distinct:zorbex+zorbey"]]),
        ("recordedWords", ["zorbex", "Dana Whitfield", ["distinct:zorbex+zorbey"]]),
        (
            "recordedWords",
            ["zorbex", "Dana Whitfield", ["term:zorbex", "distinct:a+b", "distinct:a+c"]],
        ),
    )
    assert said == [
        'Recorded 1 answer by Dana Whitfield for "zorbex". It waits for Re-run.',
        'Recorded 2 answers by Dana Whitfield for "zorbex". 2 wait for Re-run.',
        'Recorded 2 answers by Dana Whitfield for "zorbex". It waits for Re-run. '
        "1 pair called unrelated is kept in the answer log. No scan reads it.",
        'Recorded 1 answer by Dana Whitfield for "zorbex". '
        "1 pair called unrelated is kept in the answer log. No scan reads it.",
        'Recorded 3 answers by Dana Whitfield for "zorbex". It waits for Re-run. '
        "2 pairs called unrelated are kept in the answer log. No scan reads them.",
    ]


def test_the_meaning_the_dictionary_already_reads_is_confirmed_and_nothing_is_written() -> None:
    row = _row("gfp", state="understood", senses=[_sense("green_fluorescent_protein", "reagent")])
    confirmed = _batch(
        row,
        choice={
            "type": "sense",
            "term": "green_fluorescent_protein",
            "theme_kind": "reagent",
            "inForce": True,
        },
    )
    assert confirmed["confirmed"] is True and confirmed["decisions"] == []
    assert confirmed["problem"] == ""
    # Another meaning is a correction.
    changed = _batch(
        row,
        choice={"type": "sense", "term": "marker", "theme_kind": "reagent", "inForce": False},
    )
    assert changed["decisions"] == [
        {"target": "term:gfp", "answer": "marker", "theme_kind": "reagent", "correcting": True}
    ]


def test_a_card_with_nothing_chosen_writes_nothing_and_says_what_to_do() -> None:
    found = _batch(_row())
    assert found["decisions"] == []
    assert found["problem"].startswith("Nothing to record yet")


def test_a_spelling_the_engine_already_folds_into_the_word_is_left_alone() -> None:
    row = _row("fig3", variants=[_variant("Fig3", "case")])
    found = _batch(
        row,
        choice={"type": "sense", "term": "figure", "theme_kind": "technique", "inForce": False},
        variants=_answers(Fig3=("same", "")),
    )
    assert [d["target"] for d in found["decisions"]] == ["term:fig3"]


def test_the_meanings_offered_are_the_words_own_then_its_near_spellings_each_once() -> None:
    row = _row(
        "hela",
        state="contested",
        senses=[
            _sense("outranked_one", "reagent", role="outranked"),
            _sense("tied_one", "organism", role="tied"),
            _sense("won", "organism"),
        ],
    )
    near = {
        "__map__": [
            ["hella", [_sense("won", "organism"), _sense("near_one", "cell_line", origin="user")]]
        ]
    }
    (options,) = _call(("meaningOptions", [row, near]))
    assert [(o["term"], o["from"]) for o in options] == [
        ("won", None),
        ("tied_one", None),
        ("outranked_one", None),
        ("near_one", "hella"),
    ]
    (line,) = _call(("optionLine", [options[3], "plate_hela_1.csv"]))
    assert line == 'your own answer · the meaning of "hella" · for example plate_hela_1.csv'
    (tied,) = _call(("optionLine", [options[1], ""]))
    assert tied == "the built-in words · tied with another meaning"


def test_an_answered_row_says_the_decision_who_gave_it_and_when() -> None:
    answered = _row(
        answered={
            "sequence": 3,
            "actor": "Dana Whitfield",
            "occurred_at": "2026-10-14T15:10:00",
            "kind": "define",
            "target": "term:zorbex",
            "said": "means zorbex stain (reagent)",
        }
    )
    said = _call(
        ("answeredWords", [answered, "Sam Okafor"]), ("answeredWords", [answered, "Dana Whitfield"])
    )
    assert said[0] == "means zorbex stain (reagent) · Dana Whitfield, 14 Oct 3:10 PM"
    assert said[1] == "means zorbex stain (reagent) · you, 14 Oct 3:10 PM"
    by = _call(("answeredBy", [answered, "Sam Okafor"]), ("answeredBy", [_row(), "Sam Okafor"]))
    assert by == ["Dana Whitfield, 14 Oct 3:10 PM", ""]


# ------------------------------------------------------------------ the source


def test_every_control_the_words_page_names_has_words_in_the_catalog() -> None:
    keys = set(re.findall(r"'data-help': '([\w.]+)'", SCRIPT))
    keys |= set(re.findall(r"'data-help': `(words\.)\$\{", SCRIPT))
    keys |= {"words.same", "words.kind", "words.unrelated"}  # the variant answers, by their name
    keys |= set(re.findall(r"\bhelp\)?, '(words\.[\w.]+)'", SCRIPT))
    keys |= set(re.findall(r"'(words\.[\w.]+)'", SCRIPT))
    keys |= set(re.findall(r"'data-help': '([\w.]+)'", QUESTIONS))
    keys |= set(re.findall(r'data-help="(section\.words)"', PAGE))
    keys.discard("words.")
    assert {"words.meter", "words.card", "words.record", "words.skip", "section.words"} <= keys
    assert keys <= set(CATALOG["entries"]), keys - set(CATALOG["entries"])


def test_the_words_styles_name_only_words_classes() -> None:
    """``words.css`` is linked on every page, so a rule on a bare ``.card`` would restyle them."""
    text = re.sub(r"/\*.*?\*/", "", STYLES, flags=re.DOTALL)
    heads = [head.strip() for head in re.findall(r"([^{};]+)\{", text)]
    selectors = [
        piece.strip() for head in heads if not head.startswith("@") for piece in head.split(",")
    ]
    loose = [selector for selector in selectors if "words-" not in selector]
    assert len(selectors) > 30 and not loose, loose


def test_words_follows_questions_in_advanced_and_the_page_loads_its_files() -> None:
    # Questions' sections are Advanced's "Lists". Words follows the full queue there.
    nav = re.search(r'aria-labelledby="sections-lists">.*?</span>\s*<span', PAGE, re.DOTALL)
    assert nav is not None
    views = re.findall(r'<button data-view="(\w+)"', nav.group(0))
    assert views.index("words") == views.index("review") + 1
    assert re.search(r"'review', 'words',", APP)
    assert '<script src="/words.js"></script>' in PAGE
    assert '<link rel="stylesheet" href="/words.css">' in PAGE
    # The map's script aliases ``#words``. This script loads after it and takes the address back.
    assert PAGE.index("/map.js") < PAGE.index("/words.js")
    assert "delete VIEW_ALIASES.words" in SCRIPT
    assert "VIEWS.words = show" in SCRIPT
    tsconfig = json.loads((WEB.parents[2] / "tsconfig.json").read_text(encoding="utf-8"))
    assert "src/ingence/web/words.js" in tsconfig["files"]
    assert SCRIPT.startswith("// @ts-check")


def test_coverage_no_longer_holds_the_word_table_and_links_to_words() -> None:
    assert "ask about it" not in APP and "defineWordControl" not in APP
    assert "unbound_words" not in APP
    assert "href: '#words'" in APP
    assert "Words that match nothing, by files" not in APP


def test_the_keys_are_listed_and_the_page_digits_stand_aside_inside_the_card() -> None:
    # Words is a section of Advanced.
    sheet = re.search(r"view: 'Advanced \u203a Words', rows: \[(.*?)\n    \]\},", KEYS, re.DOTALL)
    assert sheet is not None
    listed = re.findall(r"keys: \[([^\]]*)\]", sheet.group(1))
    flat = " ".join(listed)
    for key in ("'1'", "'9'", "'S'", "'K'", "'U'", "'Enter'", "'N'"):
        assert key in flat, key
    assert "closest('[data-own-digits]')" in KEYS
    assert "'data-own-digits'" in SCRIPT
    # The card handles the keys it lists, and only while focus is inside it.
    assert "target.closest('.words-card')" in SCRIPT
    for key in ("/^[1-9]$/", "key === 'n'", "key === 'Enter'"):
        assert key in SCRIPT, key


def test_the_page_writes_only_through_the_one_answer_route_and_reads_three_routes() -> None:
    writes = re.findall(r"\bwrite\(\s*'([^']+)'", SCRIPT)
    # Two writes, both to the answer route: a word's batch and one answer withdrawn.
    assert writes == ["/api/review/answer"] * 2
    assert "{...asked, actor: me.actor}" in SCRIPT
    reads = set(re.findall(r"api\(\s*[`']([^`'?$]+)", SCRIPT))
    assert reads <= {"/api/v1/words/queue", "/api/word", "/api/dictionary/term"}, reads
    # Every batch is a list of decisions: one request for a word and the spellings answered with it.
    assert "decisions: built.decisions" in SCRIPT
    # Nothing here applies by itself: no job is started and no rerun route is called.
    assert "/api/workspace" not in SCRIPT


def test_withdraw_is_offered_only_where_the_queue_says_so_and_only_after_a_confirmation() -> None:
    assert "answered.withdrawable ? ' ' : null" in SCRIPT
    assert "row.answered.withdrawable ?" in SCRIPT
    withdraw = SCRIPT[SCRIPT.index("async function withdraw(row)") :]
    withdraw = withdraw[: withdraw.index("async function afterWithdraw")]
    assert withdraw.index("dialogs.choose(") < withdraw.index("write('/api/review/answer'")
    assert "if (!sure) return;" in withdraw
    # A refusal says the engine's words in a sheet, and the queue is read again (the page is stale).
    assert "dialogs.tell(" in withdraw and "show()" in withdraw
    assert CATALOG["entries"]["words.withdraw"]["text"]


def test_a_glued_spelling_is_asked_its_reading_and_not_its_meaning() -> None:
    assert "words-glued-note" in SCRIPT
    assert "const unread = row.glued != null;" in SCRIPT


def test_enter_on_a_meaning_not_chosen_chooses_it_and_a_second_enter_records() -> None:
    assert "getAttribute('aria-checked') !== 'true'" in SCRIPT
    assert "chooses it first" in SCRIPT and "choose it first" in KEYS


def test_the_card_says_where_an_answer_is_kept_and_what_answering_again_replaces() -> None:
    assert "row.answered.below" in SCRIPT
    assert "Kept below" in SCRIPT and "leaves this folder" in SCRIPT
    text = CATALOG["entries"]["words.show_answered"]["text"]
    assert "same place" in text and "below that folder" in text


def test_the_queue_is_read_once_for_each_finished_run_and_not_for_each_answer() -> None:
    assert "checkedBanner" in SCRIPT and "if (said === checkedBanner) return;" in SCRIPT


def test_the_help_says_the_block_on_questions_is_ordered_by_files() -> None:
    assert "in the most files" in CATALOG["entries"]["questions.words"]["text"]


def test_the_explanation_does_not_repeat_an_illustration_of_the_design() -> None:
    assert "forty" not in SCRIPT


def test_the_page_says_nothing_of_an_assistant_and_no_emoji() -> None:
    for text in (SCRIPT, STYLES):
        assert not re.search(NAMES_OF_AN_ASSISTANT, text, re.IGNORECASE)
        assert not re.search("[\U0001f300-\U0001faff☀-➿]", text)


def test_the_words_block_heading_is_the_queues_counts() -> None:
    """The Questions page's block says how many words the engine is unsure of, and what the first
    few cover, from the queue's own counts (``questions.js`` loaded alone in Node)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed here")
    script = (
        "const vm = require('vm'); const fs = require('fs');"
        "const context = vm.createContext({console});"
        "vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);"
        "const [queue, shown] = JSON.parse(fs.readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(context.questionsPage.wordsHeadWords(queue, shown)));"
    )
    shown = [{"covered": 30}, {"covered": 44}, {"covered": 51}]
    found = subprocess.run(
        [node, "-e", script, str(WEB / "questions.js")],
        input=json.dumps([{"total": 41, "unknown_parts": 160}, shown]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(found.stdout) == (
        "41 words the engine is unsure of. The first 3 words cover 51 of 160 word parts "
        "it could not read."
    )
    one = subprocess.run(
        [node, "-e", script, str(WEB / "questions.js")],
        input=json.dumps([{"total": 1, "unknown_parts": 9}, [{"covered": 9}]]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(one.stdout).startswith(
        "1 word the engine is unsure of. The first word covers 9 of 9"
    )


def test_the_words_block_on_questions_reads_the_queue_and_links_into_words() -> None:
    assert "/api/v1/words/queue?limit=${WORDS_SHOWN}" in QUESTIONS
    assert "linkTo('#words'" in QUESTIONS
    assert "wordsBlock()," in QUESTIONS


def test_the_inspector_no_longer_says_a_definition_is_read_again_now() -> None:
    text = (WEB / "inspector.js").read_text(encoding="utf-8")
    assert "read again now" not in text
    assert text.count("waits for Re-run") >= 1 or text.count("waits for ") >= 1


def test_the_quiet_line_is_on_the_years_empty_card_and_the_landing_projects_card() -> None:
    years = (WEB / "years.js").read_text(encoding="utf-8")
    landing = (WEB / "landing.js").read_text(encoding="utf-8")
    assert "/api/v1/words/queue?limit=20" in years and "quietLine" in years
    assert "/api/v1/words/queue?limit=20" in landing
    assert "Add their meanings." in landing


# ------------------------------------------------------------------ the batch against the engine

#: Spellings the shipped Dictionary places nowhere (asserted below rather than assumed).
HEAD, SLIP, PLURAL, STRANGER = "zorbex", "zorbec", "zorbexes", "zorbey"
#: A spelling a capital divides, so its reading is a person's to give.
GLUED = "zibKelt"


@pytest.fixture
def served(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A project that read invented names holding four unknown words, and its Dashboard server."""
    from ingence.api.server import DashboardServer
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    pool = tmp_path / "pool"
    (pool / "day_one").mkdir(parents=True)
    for spelling, files in ((HEAD, 3), (SLIP, 2), (PLURAL, 2), (STRANGER, 1), (GLUED, 2)):
        for index in range(files):
            (pool / "day_one" / f"plate_{spelling}_{index + 1}.csv").write_text("a,b\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    server = DashboardServer(project.path, port=0)
    server.start()
    try:
        yield server, project
    finally:
        server.stop()


def _post(server: Any, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        server.url.rstrip("/") + "/api/review/answer", method="POST", data=json.dumps(body).encode()
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Ingence-Write", "1")
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return int(error.code), json.load(error)
        finally:
            error.close()


def test_the_batch_the_page_builds_is_accepted_by_the_engine_and_binds_after_re_run(
    served: tuple[Any, Any],
) -> None:
    """One word's card, answered as the page would: a meaning of its own, a spelling taking it, a
    spelling of one kind. The request is the body the page builds (written out here, and held
    equal to ``batchFor``'s where Node is present), and what the engine writes is checked in the
    Record and after the next reading of the names."""
    from ingence.core.schema import RecordEventKind
    from ingence.dictionary.store import DictionaryStore
    from ingence.record.log import read_events
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog
    from ingence.workspace.state import pending_rerun

    server, project = served
    for spelling in (HEAD, SLIP, PLURAL, STRANGER):
        assert DictionaryStore.builtin().look_up(spelling, scope=_project_scope()) is None
    glued = {
        "written": GLUED,
        "pieces": ["zib", "kelt"],
        "whole_files": 2,
        "alone_files": [0, 0],
        "reading": "whole",
        "tier": "words",
        "asked": True,
        "because": "x",
        "target": f"split:{GLUED.lower()}",
    }
    row = _row(
        HEAD,
        glued=glued,
        variants=[
            _variant(SLIP, "slip"),
            _variant(PLURAL, "plural"),
            _variant(STRANGER, "slip"),
        ],
    )
    decisions = [
        {
            "target": f"term:{HEAD}",
            "answer": "zorbex stain",
            "theme_kind": "reagent",
            "note": "from the plate notes",
        },
        {"target": f"term:{SLIP}", "answer": "zorbex stain", "theme_kind": "reagent"},
        {"target": f"term:{PLURAL}", "answer": "zorbex stains", "theme_kind": "reagent"},
        {"target": f"distinct:{HEAD}+{STRANGER}", "word": "unrelated"},
        {"target": f"split:{GLUED.lower()}", "word": "pieces"},
    ]
    _the_page_agrees(
        row,
        decisions,
        choice={"type": "own"},
        own={"meaning": "zorbex stain", "kind": "reagent", "below": None},
        variants=_answers(
            **{SLIP: ("same", ""), PLURAL: ("kind", "zorbex stains"), STRANGER: ("unrelated", "")}
        ),
        split="pieces",
        note="from the plate notes",
    )
    # One request, every decision the card holds: the engine writes each on its own terms.
    status, answered = _post(server, {"actor": "Dana Whitfield", "decisions": decisions})
    assert status == 201, answered
    assert answered["refused"] == []
    assert [event["target"] for event in answered["written"]] == [d["target"] for d in decisions]
    events = {event.target: event for event in read_events(project.path)}
    assert events[f"term:{HEAD}"].kind is RecordEventKind.DEFINE
    # A spelling given the meaning the head was just given names a meaning that exists by then.
    assert events[f"term:{SLIP}"].kind is RecordEventKind.ASSIGN
    assert events[f"term:{PLURAL}"].kind is RecordEventKind.DEFINE
    # Two spellings called unrelated are kept apart; a divided spelling's reading is the person's.
    assert events[f"distinct:{HEAD}+{STRANGER}"].kind is RecordEventKind.REJECT
    split_payload = json.loads(events[f"split:{GLUED.lower()}"].payload)
    assert split_payload["reading"] == "pieces" and split_payload["answer"] == "separate words"
    assert json.loads(events[f"term:{HEAD}"].payload)["note"] == "from the plate notes"
    assert {event.actor for event in events.values()} == {"Dana Whitfield"}
    # Nothing applies until Re-run, and the badge counts what waits.
    waiting = pending_rerun(project.path)
    assert waiting is not None and waiting.count >= 4
    report = run_census(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", rebind=True
    )
    with open_catalog(project.path, run_id=report.run_id) as catalog:
        bound = dict(
            catalog.query(
                "SELECT DISTINCT c.normalized_value, b.term FROM dictionary_bindings b "
                "JOIN components c USING (component_id) WHERE c.normalized_value IN (?, ?, ?, ?)",
                [HEAD, SLIP, PLURAL, STRANGER],
            ).fetchall()
        )
    # The head and the spelling that is the same thing bind to one meaning; the spelling of one
    # kind has its own; the stranger, answered with nothing, is still unknown.
    assert bound[HEAD] == bound[SLIP] == "zorbex stain"
    assert bound[PLURAL] == "zorbex stains"
    assert bound[STRANGER] is None


def _project_scope() -> Any:
    from ingence.core.ids import PROJECT_SCOPE

    return PROJECT_SCOPE


def test_a_request_with_a_word_the_run_does_not_hold_is_refused_for_that_word_only(
    served: tuple[Any, Any],
) -> None:
    """The page says what was refused and the rest is written (it shows the engine's own words)."""
    server, _ = served
    row = _row(HEAD, variants=[_variant("quillatrix", "slip")])
    decisions = [
        {"target": f"term:{HEAD}", "answer": "stain", "theme_kind": "reagent"},
        {"target": "term:quillatrix", "answer": "stain", "theme_kind": "reagent"},
    ]
    _the_page_agrees(
        row,
        decisions,
        choice={"type": "sense", "term": "stain", "theme_kind": "reagent", "inForce": False},
        variants=_answers(quillatrix=("same", "")),
    )
    status, answered = _post(server, {"actor": "Dana Whitfield", "decisions": decisions})
    assert status == 201, answered
    assert [event["target"] for event in answered["written"]] == [f"term:{HEAD}"]
    assert [item["target"] for item in answered["refused"]] == ["term:quillatrix"]
    assert "quillatrix" in answered["refused"][0]["error"]


def _get(server: Any, path: str) -> dict[str, Any]:
    import urllib.request

    with urllib.request.urlopen(server.url.rstrip("/") + path) as response:
        found: dict[str, Any] = json.load(response)
        return found


def test_a_batch_built_from_the_queues_own_rows_answers_them_and_they_leave_the_list(
    served: tuple[Any, Any],
) -> None:
    """The contract between the queue and the page, end to end: the rows the engine serves are the
    rows the page builds a batch from (the variants' own ``distinct_target`` among them), the engine
    accepts it, and the word and the spellings answered with it leave the default list, come back
    under ``answered=only`` with the decision, and wait for Re-run."""
    server, _ = served
    queue = _get(server, "/api/v1/words/queue")
    rows = {row["word"]: row for row in queue["rows"]}
    head = rows[HEAD]
    spellings = {variant["spelling"]: variant for variant in head["variants"]}
    assert {SLIP, PLURAL} <= set(spellings), spellings
    assert head["target"] == f"term:{HEAD}" and head["state"] == "unknown"
    answers = {SLIP: ("same", ""), PLURAL: ("kind", "zorbex stains")}
    if STRANGER in spellings:
        answers[STRANGER] = ("unrelated", "")
    # The batch the page builds from these rows, in the order of the engine's spellings: the word,
    # then each spelling answered, a pair called unrelated on the target the engine gave it.
    decisions: list[dict[str, Any]] = [
        {"target": f"term:{HEAD}", "answer": "zorbex stain", "theme_kind": "reagent"}
    ]
    for variant in head["variants"]:
        spelling = variant["spelling"]
        if spelling not in answers:
            continue
        said, meaning = answers[spelling]
        if said == "unrelated":
            decisions.append({"target": variant["distinct_target"], "word": "unrelated"})
        else:
            decisions.append(
                {
                    "target": f"term:{spelling}",
                    "answer": "zorbex stain" if said == "same" else meaning,
                    "theme_kind": "reagent",
                }
            )
    _the_page_agrees(
        head,
        decisions,
        choice={"type": "own"},
        own={"meaning": "zorbex stain", "kind": "reagent", "below": None},
        variants=_answers(**answers),
    )
    status, answered = _post(server, {"actor": "Dana Whitfield", "decisions": decisions})
    assert status == 201, answered
    assert answered["refused"] == []
    assert len(answered["written"]) == len(decisions)

    left = {row["word"] for row in _get(server, "/api/v1/words/queue")["rows"]}
    assert not {HEAD, SLIP, PLURAL} & left
    back = _get(server, "/api/v1/words/queue?answered=only")
    by_word = {row["word"]: row for row in back["rows"]}
    assert {HEAD, SLIP, PLURAL} <= set(by_word)
    for word in (HEAD, SLIP, PLURAL):
        assert by_word[word]["waiting"] is True
        assert by_word[word]["answered"]["actor"] == "Dana Whitfield"
        assert by_word[word]["answered"]["said"]
    assert back["waiting"] >= 3
    # The page words what the engine said, with who and when.
    if shutil.which("node") is not None:
        (said,) = _call(("answeredWords", [by_word[HEAD], "Sam Okafor"]))
        assert said.startswith(by_word[HEAD]["answered"]["said"]) and "Dana Whitfield" in said


def test_a_glued_spellings_reading_is_written_on_the_target_the_queue_named(
    served: tuple[Any, Any],
) -> None:
    """The queue names a glued spelling's row by its reading (``split:...``) and the page writes the
    reading there. The names hold the spelling's pieces and not the spelling, so there is no word to
    give a meaning and the card asks only the reading."""
    server, _ = served
    rows = {row["word"]: row for row in _get(server, "/api/v1/words/queue")["rows"]}
    row = rows[GLUED.lower()]
    assert row["state"] == "glued" and row["glued"]["target"] == f"split:{GLUED.lower()}"
    # The names hold its pieces, so the card asks the reading only; each piece is a row.
    assert {"zib", "kelt"} <= set(rows)
    decisions = [{"target": row["glued"]["target"], "word": "whole"}]
    _the_page_agrees(row, decisions, split="whole")
    status, answered = _post(server, {"actor": "Dana Whitfield", "decisions": decisions})
    assert status == 201, answered
    assert answered["refused"] == []


def test_a_spelling_answered_with_a_word_kept_below_a_folder_is_written_below_that_folder(
    served: tuple[Any, Any],
) -> None:
    """The word's meaning kept below ``day_one`` and a spelling called the same thing: the engine
    writes both as corrections there, and neither reaches the rest of the project."""
    from ingence.core.ids import ScopeKind
    from ingence.record.log import read_events

    server, project = served
    row = _row(HEAD, variants=[_variant(SLIP, "slip")])
    place = {"correcting": True, "below": "day_one"}
    decisions = [
        {"target": f"term:{HEAD}", "answer": "zorbex stain", "theme_kind": "reagent", **place},
        {"target": f"term:{SLIP}", "answer": "zorbex stain", "theme_kind": "reagent", **place},
    ]
    _the_page_agrees(
        row,
        decisions,
        choice={"type": "own"},
        own={"meaning": "zorbex stain", "kind": "reagent", "below": "day_one"},
        variants=_answers(**{SLIP: ("same", "")}),
    )
    status, answered = _post(server, {"actor": "Dana Whitfield", "decisions": decisions})
    assert status == 201, answered
    assert answered["refused"] == []
    events = read_events(project.path)
    assert len(events) == 2
    assert {(event.scope.kind, event.scope.value) for event in events} == {
        (ScopeKind.FOLDER, "day_one")
    }


# ------------------------------------------------------------------ withdrawing one answer


def _answered_row(waiting: bool = True, **more: Any) -> dict[str, Any]:
    return _row(
        answered={
            "sequence": 7,
            "digest": "d9f0c1",
            "withdrawable": True,
            "actor": "Dana Whitfield",
            "occurred_at": "2026-10-14T15:10:00",
            "kind": "define",
            "target": "term:zorbex",
            "said": "means zorbex stain (reagent)",
            **more,
        },
        waiting=waiting,
    )


def test_the_request_that_withdraws_an_answer_names_it_by_sequence_and_digest() -> None:
    row = _answered_row()
    body = {"actor": "Sam Okafor", "withdraw": {"sequence": 7, "digest": "d9f0c1"}}
    if shutil.which("node") is not None:
        (found,) = _call(("withdrawBody", [row, "Sam Okafor"]))
        assert found == body
        # An answer the queue does not call withdrawable, and a row with none, offer nothing.
        none = _call(
            ("withdrawBody", [_answered_row(withdrawable=False), "Sam Okafor"]),
            ("withdrawBody", [_row(), "Sam Okafor"]),
        )
        assert none == [None, None]


def test_the_confirmation_and_the_refusals_are_in_words() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node is not installed here")
    asked, stale, refused, empty = _call(
        ("withdrawWords", [_answered_row(), "Sam Okafor"]),
        ("withdrawRefusal", [409, "the Record holds no answer with that sequence."]),
        ("withdrawRefusal", [400, "an answer already withdrawn cannot be withdrawn again"]),
        ("withdrawRefusal", [400, ""]),
    )
    assert asked.startswith('The answer about "zorbex" says: means zorbex stain (reagent). ')
    assert "Dana Whitfield, 14 Oct 3:10 PM" in asked
    assert "keeps it and marks it withdrawn" in asked
    assert asked.endswith(NO_RUN_READ)  # no run has read this answer: nothing waits
    assert stale.endswith("The page was out of date, so Ingence loads the list again.")
    assert refused == "an answer already withdrawn cannot be withdrawn again"
    assert empty == "Ingence gave no reason."


def test_the_confirmation_and_the_note_say_whether_the_withdrawal_waits_for_re_run() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node is not installed here")
    unread, read = _answered_row(waiting=True), _answered_row(waiting=False)
    asked_unread, asked_read, noted_unread, noted_read = _call(
        ("withdrawWords", [unread, "Sam Okafor"]),
        ("withdrawWords", [read, "Sam Okafor"]),
        ("withdrawnWords", [unread, "Sam Okafor"]),
        ("withdrawnWords", [read, "Sam Okafor"]),
    )
    # An answer no run has read is cancelled and nothing waits; one a run has read waits for Re-run.
    assert asked_unread.endswith(NO_RUN_READ) and "waits for Re-run, like every" not in asked_unread
    assert asked_read.endswith("Withdrawing waits for Re-run, like every answer.")
    assert NO_RUN_READ not in asked_read
    assert noted_unread == (
        'Withdrawn by Sam Okafor: the answer about "zorbex". The answer log keeps it. '
        + NO_RUN_READ
    )
    assert noted_read == (
        'Withdrawn by Sam Okafor: the answer about "zorbex". The answer log keeps it. '
        "The withdrawal waits for Re-run."
    )


def test_the_sentence_beside_the_button_says_after_re_run_where_a_run_has_read_the_answer() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node is not installed here")
    unread, read = _call(
        ("withdrawBeside", [_answered_row(waiting=True)]),
        ("withdrawBeside", [_answered_row(waiting=False)]),
    )
    assert unread == "Takes this answer back. Any answer before it is in force again."
    assert read == "Takes this answer back. Any answer before it is in force again after Re-run."


def _answer_and_name_it(server: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Answer the head word as the page would, and read the answer as the queue names it: the body
    that withdraws it (held equal to the page's where Node is present), and the row."""
    decisions = [{"target": f"term:{HEAD}", "answer": "zorbex stain", "theme_kind": "reagent"}]
    status, written = _post(server, {"actor": "Dana Whitfield", "decisions": decisions})
    assert status == 201, written
    assert HEAD not in {row["word"] for row in _get(server, "/api/v1/words/queue")["rows"]}
    shown = {row["word"]: row for row in _get(server, "/api/v1/words/queue?answered=only")["rows"]}
    answer = shown[HEAD]["answered"]
    assert answer["withdrawable"] is True and answer["digest"]
    body = {
        "actor": "Dana Whitfield",
        "withdraw": {"sequence": answer["sequence"], "digest": answer["digest"]},
    }
    if shutil.which("node") is not None:
        (built,) = _call(("withdrawBody", [shown[HEAD], "Dana Whitfield"]))
        assert built == body
    return body, shown[HEAD]


def test_an_answer_no_run_has_read_is_cancelled_by_withdrawing_it_and_the_word_is_listed_again(
    served: tuple[Any, Any],
) -> None:
    """The page's request on the engine's own route: the answer is taken back by its sequence and
    digest as the queue served them, the word is in the default list again with no answer, and the
    Record keeps both. Nothing waits for Re-run, since no run had read the answer. A page the
    Record has moved past is refused, in words, and writes nothing."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import read_events
    from ingence.workspace.state import pending_rerun

    server, project = served
    body, _ = _answer_and_name_it(server)
    assert pending_rerun(project.path) is not None

    # A digest the Record does not hold is a page out of date: a conflict in words, nothing written.
    stale = {**body, "withdraw": {**body["withdraw"], "digest": "not-the-digest"}}
    status, refused = _post(server, stale)
    assert status == 409, refused
    assert refused["error"]
    assert len(read_events(project.path, withdrawn=True)) == 1

    status, done = _post(server, body)
    assert status == 201, done
    back = {row["word"]: row for row in _get(server, "/api/v1/words/queue")["rows"]}
    assert HEAD in back and back[HEAD]["answered"] is None
    events = read_events(project.path, held=False, withdrawn=True, moved=False)
    assert [event.kind for event in events] == [RecordEventKind.DEFINE, RecordEventKind.WITHDRAW]
    assert pending_rerun(project.path) is None

    # The same withdrawal again names an answer that is gone: refused in words (400 or 409).
    status, again = _post(server, body)
    assert status in (400, 409), again
    assert again["error"]


def test_an_answer_a_run_has_read_is_withdrawn_by_a_change_that_waits_for_re_run(
    served: tuple[Any, Any],
) -> None:
    """Once a run has read the answer, withdrawing it is a change the next run has to read: the
    Re-run badge counts it. Until then the run still reads the word as understood, so it is not in
    the default list, and it comes back under ``understood=show`` with no answer."""
    from ingence.scan.census import run_census
    from ingence.workspace.state import pending_rerun

    server, project = served
    body, _ = _answer_and_name_it(server)
    run_census(project, run_id="run_20260101T000002Z_222222222222", started_at="t", rebind=True)
    status, done = _post(server, body)
    assert status == 201, done
    waiting = pending_rerun(project.path)
    assert waiting is not None and waiting.count >= 1
    assert any("(undone)" in row for change in waiting.changes for row in change.rows)
    assert HEAD not in {row["word"] for row in _get(server, "/api/v1/words/queue")["rows"]}
    understood = {
        row["word"]: row for row in _get(server, "/api/v1/words/queue?understood=show")["rows"]
    }
    assert HEAD in understood and understood[HEAD]["answered"] is None


def test_the_answer_that_accepts_a_pack_is_taken_by_the_engine_and_waits_for_re_run(
    served: tuple[Any, Any],
) -> None:
    """The request the page builds from the queue's header (written out here, and held equal to
    the page's where Node is present): the word ``accept`` on the pack's own target, said as an
    acceptance. The engine writes the answer to "load the pack" on every project, the pack leaves
    the header at once, and the answer waits for Re-run behind the badge. A bare ``accept`` on a
    project that loads every pack accepts nothing, which is why the page says it."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import read_events
    from ingence.workspace.state import pending_rerun

    server, project = served
    header = _get(server, "/api/v1/words/queue")
    # Every domain section loads, so the queue's header carries no pack notice. An acceptance on
    # a pack's own target is still taken, as a prior.
    assert "pack_rows" not in header and "packs" not in header
    pack = {"pack": "microscopy", "target": "pack:microscopy"}
    body = {
        "actor": "Dana Whitfield",
        "target": pack["target"],
        "word": "accept",
        "accept_pack": True,
    }
    status, written = _post(server, body)
    assert status == 201, written
    (event,) = read_events(project.path)
    assert event.target == pack["target"] and event.kind is RecordEventKind.CONFIRM
    payload = json.loads(event.payload)
    assert payload["verdict"] == "accept"
    assert payload["question"].endswith(f"load the {pack['pack']} pack")
    # What it changes waits for Re-run.
    waiting = pending_rerun(project.path)
    assert waiting is not None and waiting.count >= 1
    # A wrong word, said as an acceptance, is refused in words.
    status, refused = _post(
        server,
        {
            "actor": "Dana Whitfield",
            "target": pack["target"],
            "word": "refuse",
            "accept_pack": True,
        },
    )
    assert status == 400 and "accept" in refused["error"]
