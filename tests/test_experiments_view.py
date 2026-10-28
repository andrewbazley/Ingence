"""The Experiments tab's pure functions and its wiring.

``experiments.js`` is loaded in Node's ``vm`` with nothing of the page around it (it registers its
view only where ``VIEWS`` exists) and its functions are called on invented rows shaped as
``GET /api/v1/experiments`` sends them. Skipped where Node is missing, never failed. What needs no
Node is read from the files: the tab is second on Explore, every control the script names has words
in the help catalog, and no string of it is spelled the British way or names a decision.
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
SCRIPT = WEB / "experiments.js"

RUNNER = """
const vm = require('vm');
const fs = require('fs');
const context = vm.createContext({console});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const calls = JSON.parse(fs.readFileSync(0, 'utf8'));
const answers = calls.map(([name, args]) => context.experimentsView[name](...args));
process.stdout.write(JSON.stringify(answers));
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
    )
    return json.loads(done.stdout)


def _row(**changes: Any) -> dict[str, Any]:
    """One experiment as the route sends it, every name invented."""
    row: dict[str, Any] = {
        "id": "entry_1",
        "folder": "dana/2024-02-05_hela_dapi",
        "name": "2024-02-05_hela_dapi",
        "shown_name": "hela dapi",
        "date": "2024-02-05",
        "last_file_time": "2024-03-12T08:30:00Z",
        "files": 9,
        "raw_acquisitions": 6,
        "analysis_files": 1,
        "result_tables": 2,
        "followed": {
            "raw_found": 6,
            "followed": 2,
            "by_confirmed": 1,
            "assumed": 1,
            "share": 1 / 3,
            "said": "Followed: 2 of 6 raw files (1 by pairs a person confirmed, "
            "1 by the engine's own pairs and links, assumed right).",
        },
        "characterized": {
            "files": 9,
            "stage_read": 7,
            "stage_conflicting": 1,
            "role_read": 0,
            "role_conflicting": 0,
        },
        "no_analysis_found": None,
    }
    row.update(changes)
    return row


def test_dates_are_said_in_days_and_an_unknown_date_is_said_so() -> None:
    both, first_only, last_only, neither, same_day = _call(
        ("datesWords", [_row()]),
        ("datesWords", [_row(last_file_time=None)]),
        ("datesWords", [_row(date=None)]),
        ("datesWords", [_row(date=None, last_file_time=None)]),
        ("datesWords", [_row(last_file_time="2024-02-05T23:59:00Z")]),
    )
    assert both == "5 Feb 2024 to 12 Mar 2024"
    assert first_only == "5 Feb 2024"
    assert last_only == "undated; the last file was written 12 Mar 2024"
    assert neither == "undated"
    assert same_day == "5 Feb 2024"


def test_the_followed_count_keeps_its_denominator_and_its_two_parts() -> None:
    (view,) = _call(("followedView", [_row()]))
    assert view["known"] is True
    assert (view["raw"], view["followed"], view["confirmed"], view["assumed"]) == (6, 2, 1, 1)
    assert view["said"].startswith("Followed: 2 of 6 raw files (1 by pairs a person confirmed")
    assert view["confirmedPercent"] == pytest.approx(100 / 6)
    assert view["assumedPercent"] == pytest.approx(100 / 6)


def test_no_raw_file_found_is_unknown_and_draws_no_share() -> None:
    nothing = _row()["followed"] | {
        "raw_found": 0,
        "followed": 0,
        "by_confirmed": 0,
        "assumed": 0,
        "share": None,
        "said": "No raw file found in this experiment, so there is nothing to follow.",
    }
    (view,) = _call(("followedView", [_row(followed=nothing)]))
    assert view["known"] is False
    assert (view["confirmedPercent"], view["assumedPercent"]) == (0, 0)
    assert view["said"] == nothing["said"]


def test_characterization_lines_carry_the_experiments_files_as_their_denominator() -> None:
    (lines,) = _call(("characterizedLines", [_row()]))
    assert lines == [
        "Stage read in 7 of 9 files; results in conflict for 1",
        "Role read in 0 of 9 files",
    ]
    (one,) = _call(
        (
            "characterizedLines",
            [_row(characterized={**_row()["characterized"], "files": 1, "stage_read": 1})],
        )
    )
    assert one[0] == "Stage read in 1 of 1 file; results in conflict for 1"


def test_a_breakdown_lists_only_the_classes_that_hold_files_in_the_engines_order() -> None:
    items = [
        {"key": "raw", "label": "raw acquisitions", "files": 6},
        {"key": "analysis", "label": "analysis files", "files": 0},
        {"key": "other", "label": "another stage", "files": 1},
        {"key": "none", "label": "no stage read", "files": 2},
    ]
    (lines,) = _call(("classLines", [items]))
    assert lines == [["raw acquisitions", "6"], ["another stage", "1"], ["no stage read", "2"]]


def test_the_counts_line_and_the_flags_say_what_the_row_holds() -> None:
    (counts,) = _call(("countsWords", [_row()]))
    assert counts == "6 raw · 1 analysis · 2 results tables"
    listed, answered, nothing = _call(
        ("flagsOf", [_row(no_analysis_found="listed")]),
        ("flagsOf", [_row(no_analysis_found="answered")]),
        ("flagsOf", [_row()]),
    )
    assert listed == [{"key": "listed", "text": "On the No analysis found list", "person": False}]
    # A person said so: the one flag drawn in the person's color.
    assert answered == [
        {"key": "answered", "text": "Analysis said to be elsewhere", "person": True}
    ]
    assert nothing == []


def _query(**chosen: Any) -> dict[str, Any]:
    """The page's query as ``defaults()`` has it, with what was chosen."""
    return {
        "sort": "name",
        "order": "asc",
        "person": "",
        "line": "",
        "topic": "",
        "state": "",
        "q": "",
        "offset": 0,
        "limit": 50,
    } | chosen


def test_the_query_names_only_what_was_chosen_and_a_sort_turns_round_when_chosen_again() -> None:
    plain, chosen, topical = _call(
        ("queryString", [_query()]),
        (
            "queryString",
            [
                _query(
                    sort="files",
                    order="desc",
                    person="dana",
                    line="t3",
                    state="raw_only",
                    q="hela",
                    offset=50,
                )
            ],
        ),
        ("queryString", [_query(topic="hg2134a6d53a442b")]),
    )
    assert plain == "sort=name&order=asc&offset=0&limit=50"
    assert (
        chosen
        == "sort=files&order=desc&person=dana&line=t3&state=raw_only&q=hela&offset=50&limit=50"
    )
    assert topical == "sort=name&order=asc&topic=hg2134a6d53a442b&offset=0&limit=50"
    again, other, size = _call(
        ("nextSort", [{"sort": "name", "order": "asc"}, "name"]),
        ("nextSort", [{"sort": "name", "order": "asc"}, "date"]),
        ("nextSort", [{"sort": "name", "order": "asc"}, "files"]),
    )
    assert again == {"sort": "name", "order": "desc"}
    assert other == {"sort": "date", "order": "asc"}
    assert size == {"sort": "files", "order": "desc"}


def test_a_topic_chosen_is_a_filter_and_the_starting_query_holds_none() -> None:
    start, filtered, topical, sorted_only = _call(
        ("defaults", []),
        ("isFiltered", [_query(q="hela")]),
        ("isFiltered", [_query(topic="hg2134a6d53a442b")]),
        ("isFiltered", [_query(sort="files", order="desc", offset=50)]),
    )
    assert start == _query()
    assert (filtered, topical, sorted_only) == (True, True, False)


def _topic(
    name: str, experiments: int | None, *, complete: bool = True, **said: Any
) -> dict[str, Any]:
    """One topic as the route serves it, every name invented."""
    return {
        "id": f"hg{name.replace(' ', '')}",
        "name": name,
        "experiments": experiments,
        "read": experiments is not None,
        "complete": complete,
        "said": said.get("said", f"{experiments} of 6 experiments, 21 files."),
    }


def test_a_topic_option_marks_a_count_that_may_be_more_and_one_not_read() -> None:
    whole, partial, unread, large = _call(
        ("topicOptionLabel", [_topic("Cell lines", 3)]),
        ("topicOptionLabel", [_topic("One and a line", 1, complete=False)]),
        ("topicOptionLabel", [_topic("Only a line", None, complete=False)]),
        ("topicOptionLabel", [_topic("Everything", 1200)]),
    )
    assert whole == "Cell lines (3)"
    assert partial == "One and a line (at least 1)"
    # Unknown is not 0: a topic nothing of which was read has no number.
    assert unread == "Only a line (not read)"
    assert large == "Everything (1,200)"


def test_a_row_with_no_topic_says_none_only_where_every_topic_was_read() -> None:
    none, hedged, unread = _call(
        ("noTopicWords", [True, False]),
        ("noTopicWords", [True, True]),
        ("noTopicWords", [False, False]),
    )
    assert none == "none"
    assert hedged == "none of the collections read"
    assert unread == "not read"


def test_each_unread_topic_is_named_with_the_sentence_the_engine_served() -> None:
    read = _topic("Cell lines", 2)
    unread = _topic(
        "Only a line", None, complete=False, said="Not read: the lines of work were not computed."
    )
    found = {
        "topics": [
            read,
            unread,
            _topic("A census one", None, complete=False, said="Not read: no rules."),
        ]
    }
    (lines, nothing) = _call(
        ("unreadTopicLines", [found]), ("unreadTopicLines", [{"topics": [read]}])
    )
    assert lines == [
        "\u201cOnly a line\u201d: Not read: the lines of work were not computed.",
        "\u201cA census one\u201d: Not read: no rules.",
    ]
    assert nothing == []


def test_the_no_match_card_leads_with_why_where_the_topic_asked_for_cannot_be_read() -> None:
    read = _topic("Cell lines", 2)
    unread = _topic("Only a line", None, complete=False)
    found = {"topics_read": True, "topics": [read, unread]}
    plain, asked_read, asked_unread, gone, none_read, filtered_otherwise = _call(
        ("noMatchSentence", [found, _query()]),
        ("noMatchSentence", [found, _query(topic=read["id"])]),
        ("noMatchSentence", [found, _query(topic=unread["id"])]),
        ("noMatchSentence", [found, _query(topic="hgdeleted")]),
        ("noMatchSentence", [{"topics_read": False, "topics": []}, _query(topic=read["id"])]),
        ("noMatchSentence", [found, _query(q="hela")]),
    )
    assert plain == "No experiment matches these filters."
    # A topic read whole that holds none of what the other filters keep is a plain no match.
    assert asked_read == "No experiment matches these filters."
    assert asked_unread == "Ingence could not read this collection."
    assert gone == "This collection no longer exists."
    assert none_read == "Ingence could not read this collection."
    assert filtered_otherwise == "No experiment matches these filters."


def test_the_range_and_the_headline_always_give_the_whole_they_are_part_of() -> None:
    whole, narrowed, none, single = _call(
        ("rangeWords", [{"offset": 0, "shown": 50, "matching": 120, "total": 120}]),
        ("rangeWords", [{"offset": 50, "shown": 12, "matching": 62, "total": 120}]),
        ("rangeWords", [{"offset": 0, "shown": 0, "matching": 0, "total": 120}]),
        ("rangeWords", [{"offset": 4, "shown": 1, "matching": 5, "total": 5}]),
    )
    assert whole == "Showing 1 to 50 of 120 experiments."
    assert narrowed == "Showing 51 to 62 of 62 matching, of 120 experiments in all."
    assert none == "No experiment matches, of 120 experiments in all."
    assert single == "Showing 5 of 5 experiments."
    summary = {
        "summary": {
            "experiments": 6,
            "files": 22,
            "files_outside": 1,
            "judged": 2,
            "no_analysis_found": 1,
        }
    }
    head, standing = _call(("headline", [summary]), ("standingWords", [summary]))
    assert head == "6 experiments found, holding 22 files. 1 file found lies in no experiment."
    assert standing == "1 of 2 experiments with enough raw files show no analysis found."
    (quiet,) = _call(("standingWords", [{"summary": {**summary["summary"], "judged": 0}}]))
    assert quiet == ""


# ---------------------------------------------------------------------------
# What needs no Node.
# ---------------------------------------------------------------------------


def test_the_tab_is_first_on_its_own_page_before_collections() -> None:
    """Experiments is a page of its own, first; Collections (topics) after it."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    experiments = re.search(r'data-sections-of="experiments"[^>]*>(.*?)</nav>', page, re.DOTALL)
    assert experiments is not None
    sections = re.findall(r'<button data-view="(\w+)"', experiments.group(1))
    assert sections == ["experiments", "topics"]
    app = (WEB / "app.js").read_text(encoding="utf-8")
    listed = re.search(r"  experiments: \[([^\]]*)\]", app)
    assert listed is not None
    views = re.findall(r"'(\w+)'", listed.group(1))
    assert views == ["experiments", "topics"]
    assert '<script src="/experiments.js"></script>' in page
    assert '<link rel="stylesheet" href="/experiments.css">' in page


def test_every_control_the_script_names_has_words_in_the_help_catalog() -> None:
    catalog = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))["entries"]
    script = SCRIPT.read_text(encoding="utf-8")
    keys = set(re.findall(r"'data-help': '([\w.]+)'", script))
    keys |= set(re.findall(r"help = '([\w.]+)'", script))
    # The filters are built by one helper: selectNode(name, label, help key, ...).
    keys |= set(re.findall(r"selectNode\('\w+', '[^']+', '([\w.]+)'", script))
    keys |= {"section.experiments"}
    assert "experiments.followed" in keys and "experiments.pairs" in keys
    assert {"experiments.topics", "experiments.topic_chip", "experiments.filter_topic"} <= keys
    assert keys <= set(catalog), keys - set(catalog)
    for key in keys:
        assert catalog[key]["title"].strip() and catalog[key]["text"].strip(), key
        assert "http" not in catalog[key]["text"], key
    # Every help key the page's nav button names exists too.
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'data-view="experiments" data-help="section.experiments"' in page


def test_the_script_is_typed_and_checked_with_the_others() -> None:
    assert SCRIPT.read_text(encoding="utf-8").startswith("// @ts-check")
    tsconfig = json.loads((WEB.parents[2] / "tsconfig.json").read_text(encoding="utf-8"))
    assert "src/ingence/web/experiments.js" in tsconfig["files"]


def test_the_script_uses_the_shared_states_and_draws_no_probability_looking_number() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    # Empty, loading and error states are the app's, never a spinner or a blank pane of its own.
    assert "states.say(" in script and "states.loading(" in script and "states.failed(" in script
    # A share is never printed as a percent: the counts and their denominators are.
    assert "toFixed(0)}%" not in script and "Math.round(100" not in script
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", script), "no emoji"
