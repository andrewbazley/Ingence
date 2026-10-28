"""Warnings counted by kind, in bounded memory.

The capture around a reading counts warnings as messages arrive, keeping a bounded set of distinct
ones. A run's warnings are tallied by a normalised signature (the category and the message with
numbers and quoted paths replaced), with the first, the last and a small sample. A capture that
appended every log record and every Python warning to a list would hold every message in memory
first when a library warns once per page of a pathological file.
"""

from __future__ import annotations

import logging
import warnings

from ingence.extractors.captured import (
    MESSAGES_KEPT,
    MESSAGES_TRACKED,
    SAMPLE_KEPT,
    SIGNATURES_KEPT,
    WarningTallies,
    captured,
    signature_of,
    summarise,
)

#: One kind of warning, raised this many times: large enough that a list of every message is a
#: cost worth bounding, and the count must still come back exact.
MANY = 100_000


def test_a_hundred_thousand_warnings_of_one_kind_cost_a_bounded_capture() -> None:
    with captured() as said:
        for index in range(MANY):
            warnings.warn(f"page {index} of '/kelt/vorn_{index}.pdf' is damaged", stacklevel=1)
    assert said.total == MANY
    assert len(said.counts) <= MESSAGES_TRACKED
    kept = summarise(said)
    assert len(kept) <= MESSAGES_KEPT + 1
    assert kept[-1] == f"and {MANY - MESSAGES_KEPT} more"


def test_logged_records_are_counted_as_they_arrive_too() -> None:
    noisy = logging.getLogger("zib.library")
    with captured() as said:
        for index in range(MANY):
            noisy.warning("a wrong pointing object %d", index)
    assert said.total == MANY
    assert len(said.counts) <= MESSAGES_TRACKED


def test_a_hundred_thousand_warnings_of_one_kind_are_one_tally_with_the_exact_count() -> None:
    tallies = WarningTallies()
    for index in range(MANY):
        tallies.add("extract.brisa", f"UserWarning: page {index} of '/kelt/vorn_{index}.pdf'")
    (tally,) = tallies.tallies()
    assert tally.count == MANY
    assert tally.signature == "UserWarning: page <n> of <path>"
    assert tally.source == "extract.brisa"
    assert tally.first_message == "UserWarning: page 0 of '/kelt/vorn_0.pdf'"
    assert tally.last_message == f"UserWarning: page {MANY - 1} of '/kelt/vorn_{MANY - 1}.pdf'"
    assert len(tally.sample) <= SAMPLE_KEPT


def test_a_summarised_line_counts_as_many_as_it_says() -> None:
    tallies = WarningTallies()
    tallies.add("extract.brisa", "zib.library: a wrong pointing object (x100)")
    tallies.add("extract.brisa", "and 7 more")
    counted = {tally.signature: tally.count for tally in tallies.tallies()}
    assert counted["zib.library: a wrong pointing object"] == 100
    assert counted["messages not kept by kind"] == 7


def test_the_kinds_kept_are_bounded_and_the_rest_counted_together() -> None:
    tallies = WarningTallies()
    for index in range(SIGNATURES_KEPT + 50):
        tallies.add("extract.brisa", f"kind{'x' * (index % 7)} {chr(97 + index % 26)}{index}w")
    kept = tallies.tallies()
    assert len(kept) <= SIGNATURES_KEPT + 1
    assert sum(tally.count for tally in kept) == SIGNATURES_KEPT + 50


def test_a_signature_replaces_numbers_and_quoted_paths_only() -> None:
    assert signature_of("UserWarning: 12 fonts in 'a/b.pdf' at 0x1F") == (
        "UserWarning: <n> fonts in <path> at <n>"
    )
    assert signature_of('trell: cannot read "C:\\zib\\x.tif"') == "trell: cannot read <path>"
    assert signature_of("a 'quoted word' stays") == "a 'quoted word' stays"


def test_a_dive_states_what_its_readers_said_as_tallies_on_its_run(tmp_path) -> None:
    from fixtures.hostile import HOSTILE_SUFFIX, register_all
    from ingence.core.project import Project
    from ingence.core.schema import RunRecord
    from ingence.core.settings import ExtractionSettings, Settings
    from ingence.extractors.base import EXTRACTORS
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen
    from ingence.storage.catalog import open_catalog
    from ingence.storage.index_store import record_from_row

    names = register_all()
    try:
        pool = tmp_path / "pool"
        pool.mkdir()
        for name in ("talkative", "chatty"):
            (pool / f"{name}{HOSTILE_SUFFIX}").write_text("x")
        project = Project.create(
            tmp_path / "project",
            created_at="t",
            roots=[pool],
            settings=Settings(
                extraction=ExtractionSettings(
                    enabled=True, adapters=("hostile_noisy",), use_subprocess_isolation=False
                )
            ),
        )
        run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
        run_deepen(
            project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60
        )
    finally:
        for name in names:
            EXTRACTORS.unregister(name)
    with open_catalog(project.path) as catalog:
        (row,) = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
    counted = {
        (tally.source, tally.signature): tally.count
        for tally in record_from_row(row, RunRecord).warnings
    }
    # A hundred a file, two files: counted exactly, as one kind.
    assert (
        counted[("extract.hostile_noisy", "noisy.library: ignoring a wrong pointing object")] == 200
    )
    assert (
        counted[("extract.hostile_noisy", "UserWarning: palette images should be converted")] == 2
    )
