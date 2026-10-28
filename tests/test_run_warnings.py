"""The census and inference count what libraries say, as the dive does.

The run record has a ``warnings`` field (``WarningTally``: exact counts, bounded words). A census or
an inference during which a library logged or warned counts it onto its own run record and prints
none of it to the terminal.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.core.schema import RunRecord
from ingence.infer.run import run_inference
from ingence.scan import census
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row

if TYPE_CHECKING:
    from pathlib import Path

_CENSUS = "run_20260101T000001Z_111111111111"
_INFERENCE = "run_20260101T000002Z_222222222222"


def _project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    for n in range(3):
        (pool / "kelt" / f"vorn_{n}.csv").write_text("a,b\n1,2\n")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool])


def _tallies(project: Project, run_id: str) -> dict[tuple[str, str], int]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        (row,) = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
    return {
        (tally.source, tally.signature): tally.count
        for tally in record_from_row(row, RunRecord).warnings
    }


def _talking(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = census.stat_facts

    def talkative(result: object) -> object:
        warnings.warn("zib palette is deprecated in 3 places", UserWarning, stacklevel=1)
        return facts(result)  # type: ignore[arg-type]

    monkeypatch.setattr(census, "stat_facts", talkative)


def test_an_outer_error_filter_still_wins_over_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suite runs under ``error``: a filter the caller set stands over the pass's count, which
    only catches what no outer filter decided.
    """
    project = _project(tmp_path)
    _talking(monkeypatch)
    with pytest.raises(UserWarning, match="zib palette"):
        run_census(project, run_id=_CENSUS, started_at="t")


@pytest.mark.filterwarnings("always::UserWarning")
def test_a_census_counts_what_a_library_said_onto_its_run_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    facts = census.stat_facts

    def talkative(result: object) -> object:
        warnings.warn("zib palette is deprecated in 3 places", UserWarning, stacklevel=1)
        logging.getLogger("trell.library").warning("ignoring pointer at offset %d", 17)
        return facts(result)  # type: ignore[arg-type]

    monkeypatch.setattr(census, "stat_facts", talkative)
    run_census(project, run_id=_CENSUS, started_at="t")
    counted = _tallies(project, _CENSUS)
    files = 3
    assert counted[("census", "UserWarning: zib palette is deprecated in <n> places")] >= files
    assert counted[("census", "trell.library: ignoring pointer at offset <n>")] >= files
    printed = capsys.readouterr()
    assert "zib palette" not in printed.err + printed.out
    assert "ignoring pointer" not in printed.err + printed.out


def test_a_census_no_library_spoke_during_states_no_warnings(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_census(project, run_id=_CENSUS, started_at="t")
    assert _tallies(project, _CENSUS) == {}


def test_an_inference_counts_what_a_library_said_onto_its_run_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.infer import run as inference

    project = _project(tmp_path)
    run_census(project, run_id=_CENSUS, started_at="t")
    vocabulary = inference._vocabulary

    def talkative(project: Project) -> object:
        for n in range(1000):
            logging.getLogger("brisa.parser").warning("row %d had no value", n)
        return vocabulary(project)

    monkeypatch.setattr(inference, "_vocabulary", talkative)
    run_inference(project, run_id=_INFERENCE, started_at="t")
    counted = _tallies(project, _INFERENCE)
    # A thousand messages of one kind: counted exactly, kept boundedly, as one tally.
    assert counted[("inference", "brisa.parser: row <n> had no value")] == 1000


def test_a_warning_the_engine_itself_raises_is_not_counted_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count is of what libraries say. A warning attributed to the engine's own code keeps
    the filters it had outside the pass, so the suite's ``error`` filter still stops it: the
    count must not become a place the engine's own defects go quiet."""
    project = _project(tmp_path)
    facts = census.stat_facts

    def own(result: object) -> object:
        warnings.warn_explicit(
            "kelt is deprecated", DeprecationWarning, "census.py", 1, module="ingence.scan.census"
        )
        return facts(result)  # type: ignore[arg-type]

    monkeypatch.setattr(census, "stat_facts", own)
    with pytest.raises(DeprecationWarning, match="kelt is deprecated"):
        run_census(project, run_id=_CENSUS, started_at="t")


# -- whether a run's empty warnings are a count or unknown ------------------------------------


def test_a_census_or_inference_before_they_counted_reads_as_unknown() -> None:
    """A census or inference written at schema 1.22, before the count existed, never counted, and
    its empty warnings said "none".

    A run says whether it counted. A run that does not say is read by what only a dive states at
    1.22, the choice of its digest, since only the dive counted then.
    """
    from ingence.core.run_records import build_run_record, warnings_counted

    # A pass says it counted; a record built without saying says nothing.
    built = build_run_record(run_id=_CENSUS, started_at="t", profile="census", settings=None)
    assert built.warnings_tallied is None
    assert warnings_counted(built.model_copy(update={"warnings_tallied": True}))
    older_census = built
    assert not warnings_counted(older_census)
    older_dive = older_census.model_copy(update={"hash_mode": "need", "hash_mode_set_by": "need"})
    assert warnings_counted(older_dive)
    uncounted = build_run_record(
        run_id=_CENSUS, started_at="t", profile="remote", settings=None, warnings_tallied=False
    )
    assert not warnings_counted(uncounted)


def test_a_census_and_an_inference_say_they_counted(tmp_path: Path) -> None:
    from ingence.core.run_records import warnings_counted

    project = _project(tmp_path)
    run_census(project, run_id=_CENSUS, started_at="t")
    run_inference(project, run_id=_INFERENCE, started_at="t")
    for run_id in (_CENSUS, _INFERENCE):
        with open_catalog(project.path, run_id=run_id) as catalog:
            (row,) = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
        record = record_from_row(row, RunRecord)
        assert record.warnings_tallied is True
        assert warnings_counted(record)
