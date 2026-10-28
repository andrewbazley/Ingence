"""An Office template's creation stamp is never a file's time.

A document's own creation stamp written at exactly midnight UTC, long before the same document's own
modification stamp, is the stamp of the template it was made from. A blank workbook or presentation
carries its maker's date, years before anyone saves a document from it. So the stamp is kept as what
the package says and never believed as when the file first existed.

The package is built by hand from the parts the Open Packaging Conventions name, with invented
values, and run through a real ``init``, ``index``, ``dive`` and ``infer``. Then the rule alone is
run on records built by hand, for the edges the judgement draws. Every name here is invented.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from fixtures.office_packages import presentation, workbook
from ingence.cli.main import app
from ingence.core.schema import Entry, Observation
from ingence.infer.subjects import EntryRecords
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

load_builtins()

#: When the invented files were last saved, as the filesystem says (1 May 2024, 10:12 UTC).
SAVED = dt.datetime(2024, 5, 1, 10, 12, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def dived(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project over one folder holding a workbook and a presentation made from blank templates."""
    base = tmp_path_factory.mktemp("template_stamps")
    folder = base / "pool" / "kelt"
    folder.mkdir(parents=True)
    workbook(
        folder / "brisa_plan.xlsx", created="2006-09-16T00:00:00Z", modified="2024-05-01T10:12:00Z"
    )
    presentation(
        folder / "zeph_talk.pptx", created="2006-08-16T00:00:00Z", modified="2024-05-01T10:12:00Z"
    )
    for made in folder.iterdir():
        os.utime(made, (SAVED.timestamp(), SAVED.timestamp()))
    project = base / "project"
    runner = CliRunner()
    for command in (
        ["init", str(project), "--root", str(base / "pool")],
        ["index", "-p", str(project), "--quiet"],
        ["dive", "-p", str(project), "--budget", "60"],
        ["infer", "-p", str(project)],
    ):
        done = runner.invoke(app, command)
        assert done.exit_code == 0, (command, done.output)
    return project


def _earliest(project: Path, name: str) -> dict[str, object]:
    with open_catalog(project) as catalog:
        rows = catalog.query(
            "SELECT c.object_reference FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'earliest_credible_time' AND e.name = ? "
            "AND e.entry_kind <> 'derived'",
            [name],
        ).fetchall()
    assert len(rows) == 1, rows
    said: dict[str, object] = json.loads(rows[0][0])
    return said


@pytest.mark.parametrize(
    ("name", "day"),
    [("brisa_plan.xlsx", "16 September 2006"), ("zeph_talk.pptx", "16 August 2006")],
)
def test_a_blank_template_s_creation_stamp_is_never_the_file_s_time(
    dived: Path, name: str, day: str
) -> None:
    said = _earliest(dived, name)
    assert str(said["at"]).startswith("2024-"), said
    assert said["source"] != "document_clock"
    not_believed = said.get("not_believed")
    assert isinstance(not_believed, list) and len(not_believed) == 1, said
    assert f"{day} at midnight" in str(not_believed[0])
    assert "template" in str(not_believed[0])
    assert "the setting for the least gap between the two dates" in str(not_believed[0])
    assert "template_stamp_minimum_gap_days" not in str(not_believed[0])


def test_the_template_s_stamp_is_kept_as_what_the_package_says(dived: Path) -> None:
    """The fact stays, and only the interpretation changes.

    A fact that was read and a conclusion drawn from it are never stored in the same field.
    """
    with open_catalog(dived) as catalog:
        kept = catalog.query(
            "SELECT o.value_integer FROM observations o JOIN entries e ON e.entry_id = o.entry_id "
            "WHERE e.name = 'brisa_plan.xlsx' AND o.key LIKE '%.created_ns'"
        ).fetchall()
    stamp = dt.datetime(2006, 9, 16, tzinfo=dt.UTC)
    assert [int(row[0]) for row in kept] == [int(stamp.timestamp()) * 1_000_000_000]


# The rule alone, on records built by hand.

#: Nanoseconds in a day, and the instants the cases below use.
DAY = 86_400_000_000_000
STAMP_2006 = int(dt.datetime(2006, 9, 16, tzinfo=dt.UTC).timestamp()) * 1_000_000_000
SAVE_2024 = int(SAVED.timestamp()) * 1_000_000_000


def _records(
    created: int,
    modified: int | None,
    *,
    namespace: str = "table",
    observer: str = "spreadsheet",
    resolution: str = "second",
    file_modified: int = SAVE_2024,
) -> EntryRecords:
    entry = Entry(
        entry_id="entry_vorn",
        root_id="root",
        relative_path="kelt/vorn.xlsx",
        name="vorn.xlsx",
        depth=2,
        entry_type="file",
        extension="xlsx",
        first_seen_run="r",
        last_seen_run="r",
        modified_ns=file_modified,
        event_time_ns=created,
        event_time_source="document",
    )
    stamps: dict[str, int | str | None] = {
        "created_ns": created,
        "created_resolution": resolution,
        "modified_ns": modified,
    }
    observations = tuple(
        Observation(
            observation_id=f"obs_{key}",
            entry_id="entry_vorn",
            run_id="r",
            observer=observer,
            observer_version="1",
            observed_at="t",
            key=f"{namespace}.{key}",
            value_kind="text" if isinstance(value, str) else "integer",
            value_text=value if isinstance(value, str) else None,
            value_integer=value if isinstance(value, int) else None,
        )
        for key, value in stamps.items()
        if value is not None
    )
    return EntryRecords(entry=entry, observations=observations)


def _concluded(records: EntryRecords, parameters: dict[str, object] | None = None) -> dict:
    concluded = PREDICATES.get("earliest_credible_time").evaluate(records, parameters or {})
    assert concluded is not None
    said: dict[str, object] = json.loads(concluded.values[0])
    return said


def test_a_midnight_stamp_years_before_the_document_s_own_save_is_not_believed() -> None:
    said = _concluded(_records(STAMP_2006, SAVE_2024))
    assert said["source"] == "modification"
    assert said["at"] == "2024-05-01T10:12:00Z"
    assert "16 September 2006 at midnight" in str(said["not_believed"][0])


def test_a_creation_stamp_with_a_time_of_day_is_believed_however_old() -> None:
    """A document begun years ago and still edited is long-lived, not a template's."""
    begun = STAMP_2006 + 14 * 3_600_000_000_000 + 33 * 60_000_000_000
    said = _concluded(_records(begun, SAVE_2024))
    assert said == {"at": "2006-09-16T14:33:00Z", "source": "document_clock"}


def test_a_midnight_stamp_close_to_the_save_is_believed() -> None:
    created = SAVE_2024 - SAVE_2024 % DAY - 10 * DAY
    said = _concluded(_records(created, SAVE_2024))
    assert said["source"] == "document_clock"


def test_without_the_document_s_own_modification_nothing_contradicts_the_stamp() -> None:
    said = _concluded(_records(STAMP_2006, None))
    assert said == {"at": "2006-09-16T00:00:00Z", "source": "document_clock"}


def test_the_gap_is_the_rule_s_setting() -> None:
    created = SAVE_2024 - SAVE_2024 % DAY - 10 * DAY
    said = _concluded(_records(created, SAVE_2024), {"template_stamp_minimum_gap_days": 5})
    assert said["source"] == "modification"
    assert "not_believed" in said


def test_a_word_processing_document_s_stamp_is_judged_the_same_way() -> None:
    said = _concluded(_records(STAMP_2006, SAVE_2024, namespace="document", observer="office"))
    assert said["source"] == "modification"


# Only an Office package's stamp, written to the second, is judged. Where it is not believed, the
# document's own last save is read beside the filesystem's times.

#: 4 March 2015 at midnight, and 1 June 2019 at 08:00, both UTC.
STAMP_2015 = int(dt.datetime(2015, 3, 4, tzinfo=dt.UTC).timestamp()) * 1_000_000_000
SAVE_2019 = int(dt.datetime(2019, 6, 1, 8, tzinfo=dt.UTC).timestamp()) * 1_000_000_000


def test_a_pdf_s_creation_date_at_midnight_is_believed() -> None:
    """The PDF reader writes the same keys; a PDF is no package made from a blank template."""
    said = _concluded(_records(STAMP_2015, SAVE_2019, namespace="document", observer="document"))
    assert said == {"at": "2015-03-04T00:00:00Z", "source": "document_clock"}


def test_a_creation_stamp_stating_a_day_alone_is_believed() -> None:
    """``created="2015-03-04"``: a date written without a time, never a template's signature."""
    said = _concluded(_records(STAMP_2015, SAVE_2019, resolution="day"))
    assert said == {"at": "2015-03-04T00:00:00Z", "source": "document_clock"}


def test_a_document_copied_years_after_its_last_save_keeps_that_save() -> None:
    """Not believed: the template's stamp; the earliest of the package's own last save (2019),
    the copy's birth and its modification (2024) is the save, named and cited."""
    concluded = PREDICATES.get("earliest_credible_time").evaluate(
        _records(STAMP_2015, SAVE_2019), {}
    )
    assert concluded is not None
    said = json.loads(concluded.values[0])
    assert said["at"] == "2019-06-01T08:00:00Z"
    assert said["source"] == "document_last_saved"
    assert "obs_modified_ns" in concluded.inputs


def test_the_readers_judged_are_the_rule_s_setting() -> None:
    said = _concluded(_records(STAMP_2006, SAVE_2024), {"template_stamp_observers": []})
    assert said["source"] == "document_clock"


def test_a_person_reading_the_file_s_times_sees_why_the_date_inside_is_not_used(
    dived: Path,
) -> None:
    """The explorer's earliest time says, in plain words, that the date written inside looks like
    the blank template's, and names the setting that governs it.
    """
    from ingence.api.v1 import context_for, dispatch

    with open_catalog(dived) as catalog:
        workbook_id = catalog.query(
            "SELECT entry_id FROM entries WHERE name = 'brisa_plan.xlsx'"
        ).fetchone()[0]
    status, card = dispatch(
        context_for(dived, settled=None, job_running=False),
        "explorer/entry",
        {"id": [workbook_id]},
    )
    assert int(status) == 200, card
    (earliest,) = [time for time in card["times"] if time["kind"] == "earliest"]
    note = str(earliest["note"])
    assert "16 September 2006 at midnight" in note, note
    assert "blank template" in note
    assert "the setting for the least gap between the two dates" in note
    assert "template_stamp_minimum_gap_days" not in note
    assert "document_clock" not in note and "UTC" not in note


def test_a_rule_reading_no_name_says_its_own_reason_for_a_part_with_no_clock() -> None:
    """A rule reading whole records but not names is never told "the name of a container member is
    not tokenised" or "the binder did not run": it says its own reason.
    """
    from ingence.core.schema import Component
    from ingence.infer.evaluate import evaluate_on_entry
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.registry import Reads
    from ingence.rulebook.resolve import effective_rulebook

    rule = next(
        held
        for held in effective_rulebook(load_revisions())
        if held.rule_id == "earliest_credible_time"
    )
    predicate = PREDICATES.get(rule.predicate)
    from ingence.core.ids import LocatorStep

    part = Entry(
        entry_id="entry_part",
        entry_kind="derived",
        locator_chain=(LocatorStep(scheme="zip", target="xl/charts/chart1.xml", ordinal=4),),
        transformation="zip",
        ordinal=4,
        parent_entry_id="entry_vorn",
        root_id="root",
        relative_path="kelt/vorn.xlsx",
        name="xl/charts/chart1.xml",
        depth=3,
        entry_type="file",
        extension="xml",
        first_seen_run="r",
        last_seen_run="r",
    )
    outcome = evaluate_on_entry(rule, predicate, EntryRecords(entry=part), reads=Reads.RECORDS)
    assert outcome.reason == "neither its content nor the filesystem recorded a time for it"
    named = Entry(
        entry_id="entry_brisa",
        root_id="root",
        relative_path="kelt/brisa.dat",
        name="brisa.dat",
        depth=2,
        entry_type="file",
        extension="dat",
        first_seen_run="r",
        last_seen_run="r",
    )
    component = Component.model_construct(component_id="component_brisa", entry_id="entry_brisa")
    unbound = EntryRecords(entry=named, components=(component,))
    outcome = evaluate_on_entry(rule, predicate, unbound, reads=Reads.RECORDS)
    assert outcome.reason == "neither its content nor the filesystem recorded a time for it"


def test_a_rule_reading_names_still_says_a_part_s_name_was_never_read() -> None:
    """The exemption is declared on the predicate (``reads_no_name``), so a rule reading Dictionary
    matches through whole records (``readings_conflict``) still says that a part's name, and an
    unmatched file's, were never read: unexamined, not examined.
    """
    from ingence.core.ids import LocatorStep
    from ingence.core.schema import Component
    from ingence.core.tiers import ResolutionStatus
    from ingence.infer.evaluate import evaluate_on_entry
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.registry import Reads
    from ingence.rulebook.resolve import effective_rulebook

    rule = next(
        held
        for held in effective_rulebook(load_revisions())
        if held.rule_id == "binding_is_ambiguous_outside_programs_and_domains"
    )
    assert rule.predicate == "readings_conflict"
    predicate = PREDICATES.get(rule.predicate)
    part = Entry(
        entry_id="entry_part",
        entry_kind="derived",
        locator_chain=(LocatorStep(scheme="zip", target="xl/charts/chart1.xml", ordinal=4),),
        transformation="zip",
        ordinal=4,
        parent_entry_id="entry_vorn",
        root_id="root",
        relative_path="kelt/vorn.xlsx",
        name="xl/charts/chart1.xml",
        depth=3,
        entry_type="file",
        extension="xml",
        first_seen_run="r",
        last_seen_run="r",
    )
    outcome = evaluate_on_entry(rule, predicate, EntryRecords(entry=part), reads=Reads.RECORDS)
    assert outcome.status is ResolutionStatus.UNEXAMINED
    assert outcome.reason == "the name of a container member is not tokenised"
    named = Entry(
        entry_id="entry_brisa",
        root_id="root",
        relative_path="kelt/brisa.dat",
        name="brisa.dat",
        depth=2,
        entry_type="file",
        extension="dat",
        first_seen_run="r",
        last_seen_run="r",
    )
    component = Component.model_construct(component_id="component_brisa", entry_id="entry_brisa")
    unmatched = EntryRecords(entry=named, components=(component,))
    outcome = evaluate_on_entry(rule, predicate, unmatched, reads=Reads.RECORDS)
    assert outcome.status is ResolutionStatus.UNEXAMINED
    assert outcome.reason == "the binder did not run for this entry"


def _copied_at(birth: dt.datetime, modified: dt.datetime, saved: dt.datetime) -> dict:
    """A workbook made from the blank template, its own last save ``saved``, its disk times
    ``birth`` and ``modified``."""

    def at(moment: dt.datetime) -> int:
        return int(moment.timestamp() * 1_000) * 1_000_000

    held = _records(STAMP_2006, at(saved), file_modified=at(modified))
    entry = held.entry.model_copy(
        update={"created_ns": at(birth), "created_time_source": "birthtime"}
    )
    return _concluded(EntryRecords(entry=entry, observations=held.observations))


def test_a_save_by_replacing_the_file_is_not_taken_over_its_birth() -> None:
    """An application writing a new file and renaming it gives a birth at the save, and a stamp cut
    to the second 0.7 s before it. The birth is the earliest time.
    """
    birth = dt.datetime(2024, 5, 1, 10, 12, 0, 700_000, tzinfo=dt.UTC)
    said = _copied_at(birth, birth, dt.datetime(2024, 5, 1, 10, 12, tzinfo=dt.UTC))
    assert said["source"] == "birth", said
    assert said["at"] == "2024-05-01T10:12:00Z"


def test_a_save_on_a_machine_hours_behind_is_not_taken_before_the_copy() -> None:
    """Copied at 10:00, saved at 12:00 by a machine whose stamp reads three hours behind (07:00):
    within the tolerance for clocks between machines, so the birth at 10:00 stands."""
    said = _copied_at(
        dt.datetime(2024, 5, 1, 10, tzinfo=dt.UTC),
        dt.datetime(2024, 5, 1, 12, tzinfo=dt.UTC),
        dt.datetime(2024, 5, 1, 7, tzinfo=dt.UTC),
    )
    assert said["source"] == "birth", said
    assert said["at"] == "2024-05-01T10:00:00Z"
