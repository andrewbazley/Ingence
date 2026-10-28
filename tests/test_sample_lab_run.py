"""One command for the sample collection: ``sample-lab DEST --run``.

It writes the collection, runs the engine over its pool in ``DEST/work``, scores it against the
collection's key, and writes ``DEST/report.json`` and ``DEST/summary.txt`` the way ``run`` does for
the small generator. The summary is the one part of the report that is the same bytes every time.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from ingence.evaluation.__main__ import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_the_sample_collection_runs_and_scores_in_one_command_with_a_byte_stable_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summaries: list[bytes] = []
    for name in ("first", "second"):
        destination = tmp_path / name
        assert main(["sample-lab", str(destination), "--tier", "S", "--seed", "1", "--run"]) == 0
        capsys.readouterr()
        assert (destination / "pool").is_dir()
        assert (destination / "work").is_dir()
        report = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        assert list(report["columns"]) == ["shipped"]
        assert report["key"]["tier"] == "S" and report["key"]["seed"] == 1
        summaries.append((destination / "summary.txt").read_bytes())
    assert summaries[0] == summaries[1]
    text = summaries[0].decode("utf-8")
    assert text.startswith("Evaluation of ingence ")
    # No time and no path: the destination's own name and the folder it sits in are nowhere.
    assert "seconds" not in text
    assert str(tmp_path) not in text and "first" not in text and "second" not in text
    assert not re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d", text)


def test_without_run_the_command_writes_the_collection_alone(tmp_path: Path) -> None:
    destination = tmp_path / "made"
    assert main(["sample-lab", str(destination), "--tier", "S", "--seed", "1"]) == 0
    assert (destination / "pool").is_dir()
    assert not (destination / "work").exists()
    assert not (destination / "summary.txt").exists()
