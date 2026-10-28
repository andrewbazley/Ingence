"""Jupyter notebooks: read, never executed, and which of many times is the event.

The clock rule is the module's decision, and these tests are its reasons written as cases: the
earliest recorded start, offered only when every executed cell recorded one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ingence.core.outcomes import CLOCK_TIER_OF, ClockTier, ScanOutcome, TimeSource
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext
from ingence.extractors.clocks import parse_w3cdtf
from ingence.extractors.limits import Limits
from ingence.extractors.notebook import execution_record

nbformat = pytest.importorskip("nbformat")

load_builtins()


def _context(path: Path, *, limits: Limits | None = None) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({".ipynb"}),
        size_bytes=path.stat().st_size,
        limits=limits,
    )


def _readings(result: object) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}  # type: ignore[attr-defined]


def _ns(raw: str) -> int:
    parsed = parse_w3cdtf(raw)
    assert parsed is not None
    return parsed[0]


def _code(source: str, count: int | None, *, start: str | None = None, end: str | None = None):
    cell = nbformat.v4.new_code_cell(source, execution_count=count)
    if start is not None:
        timing = {"iopub.execute_input": start}
        if end is not None:
            timing["shell.execute_reply"] = end
        cell.metadata["execution"] = timing
    return cell


def _write(path: Path, cells: list, **metadata: object) -> Path:
    notebook = nbformat.v4.new_notebook(cells=cells, metadata=metadata)
    nbformat.write(notebook, str(path))
    return path


def _extract(path: Path, *, limits: Limits | None = None):
    return EXTRACTORS.get("notebook").extract(_context(path, limits=limits))


# ---------------------------------------------------------------------------
# Structure, and what cells mention.
# ---------------------------------------------------------------------------


def test_a_notebook_yields_its_cells_and_the_paths_they_mention(tmp_path: Path) -> None:
    """A notebook that reads a table and writes a figure is provenance evidence.

    The paths reach ``references`` through the text extractor's own function, so
    the proposing predicates see one shape whatever read the file.
    """
    path = _write(
        tmp_path / "analysis.ipynb",
        [
            nbformat.v4.new_markdown_cell("Figures go to results/figures/overview.png"),
            _code('frame = read_csv("data/raw/plate_1.csv")', 1),
            _code('frame.plot().figure.savefig("results/figures/overview.png")', 2),
            nbformat.v4.new_raw_cell("raw"),
        ],
        kernelspec={"name": "python3", "display_name": "Python 3", "language": "python"},
        language_info={"name": "python"},
    )
    result = _extract(path)

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["notebook.cell_count"] == 4
    assert readings["notebook.code_cell_count"] == 2
    assert readings["notebook.markdown_cell_count"] == 1
    assert readings["notebook.raw_cell_count"] == 1
    assert readings["notebook.kernel"] == "python3"
    assert readings["notebook.language"] == "python"
    assert readings["notebook.format_version"].startswith("4.")
    assert result.references == ("data/raw/plate_1.csv", "results/figures/overview.png")
    assert readings["notebook.reference_count"] == 2


def test_an_error_output_is_counted_because_a_failed_run_is_evidence(tmp_path: Path) -> None:
    cell = _code("1 / 0", 1)
    cell.outputs = [
        nbformat.v4.new_output("error", ename="ZeroDivisionError", evalue="division", traceback=[])
    ]
    readings = _readings(_extract(_write(tmp_path / "failed.ipynb", [cell])))
    assert readings["notebook.output_count"] == 1
    assert readings["notebook.error_output_count"] == 1


# ---------------------------------------------------------------------------
# Which time is the event.
# ---------------------------------------------------------------------------


def test_a_notebook_every_cell_of_which_was_timed_offers_its_earliest_start(tmp_path: Path) -> None:
    """The start, because that is what tier one records for an acquisition.

    Earliest by time rather than first in the document, and the finish is the
    latest reply. Both are recorded. Only the start is offered.
    """
    path = _write(
        tmp_path / "executed.ipynb",
        [
            _code("a = 1", 2, start="2026-03-04T09:30:05.000001Z", end="2026-03-04T09:30:06Z"),
            _code("b = 2", 1, start="2026-03-04T09:30:00.000001Z", end="2026-03-04T09:30:01Z"),
            _code("c = 3", 3, start="2026-03-04T09:31:00Z", end="2026-03-04T09:32:00Z"),
        ],
    )
    result = _extract(path)

    assert result.event_time_source is TimeSource.DOCUMENT
    assert CLOCK_TIER_OF[result.event_time_source] is ClockTier.DOCUMENT
    assert result.event_time_ns == _ns("2026-03-04T09:30:00.000001Z")
    readings = _readings(result)
    assert readings["notebook.execution_finished_ns"] == _ns("2026-03-04T09:32:00Z")
    assert readings["notebook.execution_started_resolution"] == "subsecond"
    assert readings["notebook.execution_counts_ascending"] is False


def test_re_running_a_later_cell_does_not_move_the_clock(tmp_path: Path) -> None:
    """How an analysis finished last year would otherwise take this morning's date."""
    first = [
        _code("a = 1", 1, start="2025-06-01T10:00:00Z"),
        _code("a", 2, start="2025-06-01T10:00:02Z"),
    ]
    rerun = [
        _code("a = 1", 1, start="2025-06-01T10:00:00Z"),
        _code("a", 7, start="2026-09-12T08:00:00Z"),
    ]
    original = _extract(_write(tmp_path / "original.ipynb", first))
    revisited = _extract(_write(tmp_path / "revisited.ipynb", rerun))
    assert original.event_time_ns == revisited.event_time_ns == _ns("2025-06-01T10:00:00Z")


def test_timing_switched_on_halfway_offers_no_clock(tmp_path: Path) -> None:
    """A partial record does not contain its own start, so no start is offered.

    The untimed cell may have run before the earliest recorded time, which makes
    that time right sometimes and silently wrong otherwise.
    """
    path = _write(
        tmp_path / "partial.ipynb",
        [
            _code("a = 1", 1),
            _code("b = 2", 2, start="2026-03-04T09:30:00Z"),
            _code("c = 3", 3, start="2026-03-04T09:31:00Z"),
        ],
    )
    result = _extract(path)
    assert result.event_time_ns is None
    assert result.event_time_source is None
    readings = _readings(result)
    assert readings["notebook.executed_cell_count"] == 3
    assert readings["notebook.timed_cell_count"] == 2
    assert readings["notebook.execution_started_ns"] == _ns("2026-03-04T09:30:00Z")


def test_an_interactive_notebook_with_no_timing_says_so_rather_than_nothing(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "interactive.ipynb", [_code("a = 1", 4), _code("a", 9)])
    result = _extract(path)
    readings = _readings(result)
    assert result.event_time_ns is None
    assert readings["notebook.execution_time_absent"] is True
    assert "notebook.never_executed" not in readings


def test_a_notebook_never_run_is_a_finding_distinct_from_one_run_untimed(tmp_path: Path) -> None:
    """A template and an analysis look alike by name, and this separates them."""
    readings = _readings(_extract(_write(tmp_path / "template.ipynb", [_code("a = 1", None)])))
    assert readings["notebook.never_executed"] is True
    assert "notebook.execution_time_absent" not in readings


def test_timing_left_on_cleared_cells_is_not_a_clock(tmp_path: Path) -> None:
    """The outputs those times described are gone, so the file no longer records that run."""
    cleared = _code("a = 1", None, start="2026-03-04T09:30:00Z")
    result = _extract(_write(tmp_path / "cleared.ipynb", [cleared]))
    assert result.event_time_ns is None
    assert _readings(result)["notebook.never_executed"] is True


def test_the_classic_timing_extension_and_papermill_are_read_too() -> None:
    """Three producers write execution times in three places. The rule is the same."""
    cells = [
        {
            "cell_type": "code",
            "execution_count": 1,
            "metadata": {"ExecuteTime": {"start_time": "2026-03-04T09:30:00.000Z"}},
        },
        {
            "cell_type": "code",
            "execution_count": 2,
            "metadata": {"papermill": {"start_time": "2026-03-04T09:29:00.000000"}},
        },
    ]
    record = execution_record(cells)
    assert record.executed == record.timed == 2
    assert record.event_ns == _ns("2026-03-04T09:29:00Z")


def test_an_unparseable_time_is_kept_and_withholds_the_clock() -> None:
    cells = [
        {
            "cell_type": "code",
            "execution_count": 1,
            "metadata": {"execution": {"iopub.execute_input": "last tuesday"}},
        }
    ]
    record = execution_record(cells)
    assert record.unparsed == "last tuesday"
    assert record.event_ns is None


def test_a_hostile_structure_is_read_as_absent_rather_than_raising() -> None:
    cells = [
        "not a cell",
        {"cell_type": "code", "execution_count": True, "metadata": []},
        {"cell_type": "code", "execution_count": "3", "metadata": {"execution": "soon"}},
        {"cell_type": "code", "execution_count": 1, "metadata": {"execution": {"x": 1}}},
    ]
    record = execution_record(cells)
    assert record.executed == 1
    assert record.timed == 0
    assert record.event_ns is None


# ---------------------------------------------------------------------------
# What is and is not read.
# ---------------------------------------------------------------------------


def test_a_notebook_of_the_previous_format_is_converted_rather_than_misread(
    tmp_path: Path,
) -> None:
    """Version 3 keeps cells in worksheets and calls the execution count a prompt number."""
    legacy = {
        "metadata": {"name": ""},
        "nbformat": 3,
        "nbformat_minor": 0,
        "worksheets": [
            {
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "collapsed": False,
                        "input": 'load("data/raw/plate_2.csv")',
                        "language": "python",
                        "metadata": {},
                        "outputs": [],
                        "prompt_number": 1,
                    },
                    {"cell_type": "markdown", "metadata": {}, "source": "notes"},
                ],
            }
        ],
    }
    path = tmp_path / "legacy.ipynb"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    result = _extract(path)

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["notebook.format_version"] == "3.0"
    assert readings["notebook.code_cell_count"] == 1
    assert readings["notebook.executed_cell_count"] == 1
    assert result.references == ("data/raw/plate_2.csv",)


def test_a_file_that_is_not_json_becomes_a_content_error(tmp_path: Path) -> None:
    path = tmp_path / "liar.ipynb"
    path.write_text("this is not a notebook", encoding="utf-8")
    result = _extract(path)
    assert result.outcome is ScanOutcome.CONTENT_ERROR
    assert result.error_type


def test_a_notebook_past_the_byte_ceiling_is_declined_with_the_reason(tmp_path: Path) -> None:
    """JSON has no partial parse, so reading part of one would be reading nothing."""
    path = _write(tmp_path / "large.ipynb", [_code("x = 1\n" * 200, 1)])
    result = _extract(path, limits=Limits(max_bytes_read=256))
    assert result.outcome is ScanOutcome.SKIPPED_BY_POLICY
    assert "ceiling" in result.warnings[0]


def test_reading_a_notebook_never_runs_it(tmp_path: Path) -> None:
    """Reading a notebook never executes it, even where the reader and executor share a library.

    Measured in a fresh interpreter, because this process has imported whatever
    other tests imported. The cell would create a file if anything executed it,
    and no kernel machinery may be imported by reading one.
    """
    marker = tmp_path / "executed.marker"
    path = _write(
        tmp_path / "trap.ipynb",
        [_code(f"open({str(marker)!r}, 'w').write('ran')", 1, start="2026-03-04T09:30:00Z")],
    )
    probe = (
        "import sys\n"
        "from pathlib import Path\n"
        "from ingence.extractors import load_builtins\n"
        "from ingence.extractors.base import EXTRACTORS, ExtractionContext\n"
        "load_builtins()\n"
        "path = Path(sys.argv[1])\n"
        "context = ExtractionContext(entry_id='e', path=path, relative_path=path.name,\n"
        "    extensions=frozenset({'.ipynb'}), size_bytes=path.stat().st_size)\n"
        "result = EXTRACTORS.get('notebook').extract(context)\n"
        "assert result.outcome.name == 'EXTRACTED', result\n"
        "watched = ('nbclient', 'jupyter_client', 'ipykernel', 'IPython', 'papermill', 'zmq')\n"
        "print(','.join(sorted(name for name in watched if name in sys.modules)))\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", probe, str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert finished.stdout.strip() == ""
    assert not marker.exists()


def test_an_isolated_extraction_answers_across_the_process_boundary(tmp_path: Path) -> None:
    """The declaration is only a safety story if the result survives the trip."""
    from ingence.executors.supervisor import Supervisor

    path = _write(tmp_path / "boundary.ipynb", [_code("a = 1", 1, start="2026-03-04T09:30:00Z")])
    context = _context(path)
    supervised = Supervisor(timeout_seconds=60.0).run("notebook", context)

    assert not supervised.contained
    assert supervised.result.outcome is ScanOutcome.EXTRACTED
    assert supervised.result.event_time_ns == _extract(path).event_time_ns
