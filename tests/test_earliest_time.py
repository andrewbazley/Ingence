"""When a file first existed, and a folder copied in bulk.

The rule prefers a birth over what is evidently a copy's time. It is a general, interpretable
rule that defaults to a creation time or the earliest time a file is recorded having existed,
according to its own data. "Copied in bulk" is a real phenomenon, and the rule anticipates it.
The classes, over the facts a census and a dive record: a content clock outranks the
filesystem; a birth later than the modification is a copy's, so the modification is the earlier
credible time; and a folder of files born late and born together was copied in bulk. Each value
names its source. A time before the earliest credible year, or after the run that observed the
file, is impossible: it is set aside, named, and the next source is read.

Tested at the predicate, on entries built by hand, because a birth time is a platform's to give
(macOS and Windows record one, Linux gives Python none) and the rule must read the same record
the same way everywhere. Every name here is invented.
"""

from __future__ import annotations

import json

from ingence.core.ids import Scope, ScopeKind
from ingence.core.schema import Claim, Entry
from ingence.infer.subjects import EntryRecords, FolderContents, ScopeRecords
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES

load_builtins()

#: 1 January 2025 and 1 January 2026, in nanoseconds.
EARLIER = 1_735_689_600_000_000_000
LATER = 1_767_225_600_000_000_000


def _entry(name: str = "vorn.nd2", **changes: object) -> Entry:
    fields: dict[str, object] = {
        "entry_id": f"entry_{name}",
        "root_id": "root",
        "relative_path": f"kelt/{name}",
        "name": name,
        "depth": 2,
        "entry_type": "file",
        "extension": name.rsplit(".", 1)[-1],
        "first_seen_run": "r",
        "last_seen_run": "r",
    }
    fields.update(changes)
    return Entry(**fields)


def _earliest(entry: Entry) -> dict[str, str] | None:
    concluded = PREDICATES.get("earliest_credible_time").evaluate(entry, {})
    return None if concluded is None else json.loads(concluded.values[0])


def test_a_birth_before_the_modification_is_when_the_file_first_existed() -> None:
    entry = _entry(created_ns=EARLIER, created_time_source="birthtime", modified_ns=LATER)
    assert _earliest(entry) == {"at": "2025-01-01T00:00:00Z", "source": "birth"}


def test_a_birth_after_the_modification_is_a_copy_and_the_modification_is_earlier() -> None:
    entry = _entry(created_ns=LATER, created_time_source="birthtime", modified_ns=EARLIER)
    assert _earliest(entry) == {"at": "2025-01-01T00:00:00Z", "source": "modified_before_born"}


def test_the_time_the_content_states_outranks_every_filesystem_time() -> None:
    entry = _entry(
        created_ns=EARLIER,
        created_time_source="birthtime",
        modified_ns=LATER,
        event_time_ns=EARLIER - 3_600_000_000_000,
        event_time_source="instrument",
    )
    assert _earliest(entry) == {"at": "2024-12-31T23:00:00Z", "source": "instrument_clock"}


def test_with_no_birth_recorded_the_modification_is_named_as_itself() -> None:
    entry = _entry(modified_ns=LATER)
    assert _earliest(entry) == {"at": "2026-01-01T00:00:00Z", "source": "modification"}


def test_a_folder_and_a_file_with_no_clock_conclude_nothing_and_say_why() -> None:
    predicate = PREDICATES.get("earliest_credible_time")
    folder = _entry("kelt", entry_type="directory", modified_ns=LATER)
    assert predicate.evaluate(folder, {}) is None
    assert predicate.evaluate(_entry(), {}) is None
    assert "no" in predicate.explain_abstention(_entry(), {}).reason


def _folder(files: list[Entry]) -> ScopeRecords:
    return ScopeRecords(
        scope=Scope(ScopeKind.FOLDER, "entry_kelt"),
        entries=tuple(EntryRecords(entry=entry) for entry in files),
    )


PARAMETERS = {"minimum_files": 5, "minimum_share": 0.8, "burst_window_seconds": 600}


def _copied(number: int, *, born: int, spread_s: int = 1) -> Entry:
    return _entry(
        f"brisa_{number}.tif",
        created_ns=born + number * spread_s * 1_000_000_000,
        created_time_source="birthtime",
        modified_ns=EARLIER + number,
    )


def test_a_folder_of_files_born_late_and_together_was_copied_in_bulk() -> None:
    predicate = PREDICATES.get("copied_in_bulk")
    raised = predicate.evaluate(_folder([_copied(n, born=LATER) for n in range(6)]), PARAMETERS)
    assert raised is not None and raised.values == ("copied_in_bulk",)
    assert "6 of its 6 files were born after they were last modified" in raised.reason
    assert "2026-01-01T00:00:00Z" in raised.reason


def test_files_born_late_but_apart_or_too_few_are_no_bulk_copy() -> None:
    predicate = PREDICATES.get("copied_in_bulk")
    apart = _folder([_copied(n, born=LATER, spread_s=3600) for n in range(6)])
    assert predicate.evaluate(apart, PARAMETERS) is None
    few = _folder([_copied(n, born=LATER) for n in range(4)])
    assert predicate.evaluate(few, PARAMETERS) is None
    made_here = _folder(
        [
            _entry(
                f"vorn_{n}.csv",
                created_ns=EARLIER,
                created_time_source="birthtime",
                modified_ns=LATER,
            )
            for n in range(6)
        ]
    )
    assert predicate.evaluate(made_here, PARAMETERS) is None
    assert "asked for" in predicate.explain_abstention(made_here, PARAMETERS).reason


#: A run stamped 1 January 2026, which observed the files below (its identifier leads with its
#: start).
OBSERVED = "run_20260101T000000Z_" + "a" * 12
#: 31 December 1969, 23:00 UTC: the epoch as a western time zone writes it.
EPOCH_EVE = -3_600_000_000_000
#: 1 January 2027, a year after the run that observed the file.
FUTURE = 1_798_761_600_000_000_000


def test_a_birth_from_1969_is_impossible_and_the_modification_is_read_instead() -> None:
    entry = _entry(
        created_ns=EPOCH_EVE,
        created_time_source="birthtime",
        modified_ns=EARLIER,
        last_seen_run=OBSERVED,
    )
    assert _earliest(entry) == {
        "at": "2025-01-01T00:00:00Z",
        "source": "modification",
        "set_aside": ["birth 1969-12-31T23:00:00Z"],
    }


def test_a_time_after_the_run_that_observed_the_file_is_impossible() -> None:
    entry = _entry(
        created_ns=EARLIER,
        created_time_source="birthtime",
        modified_ns=FUTURE,
        last_seen_run=OBSERVED,
    )
    assert _earliest(entry) == {
        "at": "2025-01-01T00:00:00Z",
        "source": "birth",
        "set_aside": ["modification 2027-01-01T00:00:00Z"],
    }


def test_a_file_with_only_impossible_times_concludes_nothing_and_says_why() -> None:
    entry = _entry(modified_ns=EPOCH_EVE, last_seen_run=OBSERVED)
    predicate = PREDICATES.get("earliest_credible_time")
    assert predicate.evaluate(entry, {}) is None
    assert "impossible" in predicate.explain_abstention(entry, {}).reason


def test_the_bounds_are_the_rule_s_parameters() -> None:
    entry = _entry(created_ns=EARLIER, created_time_source="birthtime", modified_ns=LATER)
    predicate = PREDICATES.get("earliest_credible_time")
    assert predicate.evaluate(entry, {"earliest_credible_year": 2026}) is not None
    later_only = json.loads(predicate.evaluate(entry, {"earliest_credible_year": 2026}).values[0])
    assert later_only["source"] == "modification"


STAGED = {
    **PARAMETERS,
    "stage_rules": ["acquisition_stage_from_format", "analysis_stage_from_derived_sense"],
    "entry_stages": ["raw", "acquisition"],
    "time_rule": "earliest_credible_time",
}


def _claim(rule: str, value: str) -> Claim:
    return Claim.model_construct(rule_id=rule, object_reference=value, resolution_status="resolved")


def _staged(stage: str, day: str, number: int) -> EntryRecords:
    time = json.dumps({"at": f"{day}T12:00:00Z", "source": "modified_before_born"})
    rule = (
        "acquisition_stage_from_format"
        if stage == "acquisition"
        else "analysis_stage_from_derived_sense"
    )
    return EntryRecords(
        entry=_copied(number, born=LATER),
        claims=(_claim(rule, stage), _claim("earliest_credible_time", time)),
    )


def test_a_copy_is_asked_about_only_where_a_later_stage_comes_before_the_raw_data() -> None:
    predicate = PREDICATES.get("copied_in_bulk")
    in_order = ScopeRecords(
        scope=Scope(ScopeKind.FOLDER, "entry_kelt"),
        entries=(
            *(_staged("acquisition", "2025-01-01", n) for n in range(3)),
            *(_staged("analysis", "2025-01-03", n + 3) for n in range(3)),
        ),
    )
    assert predicate.evaluate(in_order, STAGED) is None
    assert "information, not a question" in predicate.explain_abstention(in_order, STAGED).reason
    out_of_order = ScopeRecords(
        scope=Scope(ScopeKind.FOLDER, "entry_kelt"),
        entries=(
            *(_staged("acquisition", "2025-01-03", n) for n in range(3)),
            *(_staged("analysis", "2025-01-01", n + 3) for n in range(3)),
        ),
    )
    raised = predicate.evaluate(out_of_order, STAGED)
    assert raised is not None and "first existed on 2025-01-01" in raised.reason


def test_a_folder_copied_whole_is_concluded_with_its_day_as_information() -> None:
    entry = _entry("kelt", entry_type="directory", extension="")
    contents = FolderContents(
        entry=entry,
        dated_files_here=6,
        born_late_here=6,
        late_births_first_ns=LATER,
        late_births_last_ns=LATER + 5_000_000_000,
    )
    predicate = PREDICATES.get("copied_in_bulk_from_contents")
    concluded = predicate.evaluate(contents, PARAMETERS)
    assert json.loads(concluded.values[0]) == {"copied_on": "2026-01-01", "files": 6}
    apart = FolderContents(
        entry=entry,
        dated_files_here=6,
        born_late_here=6,
        late_births_first_ns=LATER,
        late_births_last_ns=LATER + 10**13,
    )
    assert predicate.evaluate(apart, PARAMETERS) is None
    made_here = FolderContents(
        entry=entry,
        dated_files_here=6,
        born_late_here=1,
        late_births_first_ns=LATER,
        late_births_last_ns=LATER,
    )
    assert predicate.evaluate(made_here, PARAMETERS) is None
