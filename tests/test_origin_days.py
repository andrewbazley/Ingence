"""Days, not seconds. Groups, not files.

An example that must come out as follows:

    raw_data                  processed_data            analysis_output
    created/birth: 9/11       created/birth: 9/12       created/birth: 9/14
    modified: 9/13            modified: 9/13            modified: 9/13

The days are raw_data = 9/11, processed_data = 9/12 and analysis_output = 9/13 (its modified day).
The reading assumes that analysis_output was copied on 9/14, but modified (and probably made on a
different system) on 9/13. The year and the names are invented.
"""

from __future__ import annotations

import datetime as dt
import json

from ingence.core.schema import Entry
from ingence.grouping.origin_days import day_of, days_by_stage, out_of_order, timeline
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES

load_builtins()

STAGES = ("raw", "processed", "analysis")


def _ns(month: int, day: int, hour: int = 12) -> int:
    return int(dt.datetime(2025, month, day, hour, tzinfo=dt.UTC).timestamp()) * 1_000_000_000


def _file(name: str, born: int, modified: int) -> Entry:
    return Entry(
        entry_id=f"entry_{name}",
        root_id="root",
        relative_path=f"kelt/{name}",
        name=name,
        depth=2,
        entry_type="file",
        extension=name.rsplit(".", 1)[-1],
        created_ns=born,
        created_time_source="birthtime",
        modified_ns=modified,
        first_seen_run="r",
        last_seen_run="r",
    )


def _day(entry: Entry) -> str:
    concluded = PREDICATES.get("earliest_credible_time").evaluate(entry, {})
    day = day_of(concluded.values[0])
    assert day is not None
    return day


def test_a_raw_processed_and_analysis_example_comes_out_as_a_person_reads_it() -> None:
    raw = _file("vorn_001.nd2", _ns(9, 11), _ns(9, 13))
    processed = _file("vorn_001_drift.tif", _ns(9, 12), _ns(9, 13))
    analysis = _file("vorn_001_results.csv", _ns(9, 14), _ns(9, 13))
    members = [
        ("kelt", "raw", _day(raw)),
        ("kelt", "processed", _day(processed)),
        ("kelt", "analysis", _day(analysis)),
    ]
    groups = timeline(members, stage_order=STAGES)
    assert [(g.stage, g.first) for g in groups] == [
        ("raw", "2025-09-11"),
        ("processed", "2025-09-12"),
        ("analysis", "2025-09-13"),
    ]
    assert out_of_order(groups, entry_stages=["raw"]) == []


def test_a_group_s_origin_is_its_earliest_member_s_day_and_seconds_do_not_matter() -> None:
    members = [
        ("kelt", "raw", "2025-09-16"),
        ("kelt", "raw", "2025-09-16"),
        ("kelt", "raw", "2025-09-17"),
        ("kelt try 1", "analysis", "2025-09-17"),
        ("kelt try 2", "analysis", "2025-09-18"),
    ]
    groups = timeline(members, stage_order=STAGES)
    assert [(g.group, g.first, g.last, g.files) for g in groups] == [
        ("kelt", "2025-09-16", "2025-09-17", 3),
        ("kelt try 1", "2025-09-17", "2025-09-17", 1),
        ("kelt try 2", "2025-09-18", "2025-09-18", 1),
    ]
    assert days_by_stage(groups) == {"raw": "2025-09-16", "analysis": "2025-09-17"}


def test_an_analysis_older_than_its_raw_data_is_the_one_order_worth_asking_about() -> None:
    members = [("kelt", "raw", "2025-09-16"), ("kelt copied analysis", "analysis", "2025-09-10")]
    groups = timeline(members, stage_order=STAGES)
    assert [g.group for g in out_of_order(groups, entry_stages=["raw"])] == ["kelt copied analysis"]


def test_an_unreadable_value_names_no_day() -> None:
    assert day_of("not json") is None
    assert day_of(json.dumps({"source": "birth"})) is None
