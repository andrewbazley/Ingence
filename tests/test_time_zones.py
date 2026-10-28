"""Every unzoned time is read as UTC, and a run's name is said in the machine's own zone.

The engine reads a time that states no zone as UTC wherever it reads one, and converts only to name
a run for a person. These tests hold it there under two machine zones, east and west of UTC, so a
reader that slipped into the machine's zone would be caught.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

import pytest

#: Each reader of a stored or written time, given one that states no zone, and a run's name.
_READ_IN_ZONE = """
import datetime as dt, json
from ingence.api.routes.changes import _instant as changes_instant
from ingence.api.run_names import began, run_label
from ingence.api.v1.explorer.timeline import _moment
from ingence.extractors.clocks import nanoseconds_since_epoch
from ingence.rulebook.predicates.flag import _instant as rule_instant
from ingence.workspace.rerun import instant
from ingence.workspace.restore import _instant as restore_instant

naive = "2026-01-02T03:04:05"
print(json.dumps({
    "rerun": instant(naive).isoformat(),
    "restore": restore_instant(naive).isoformat(),
    "changes": changes_instant(naive).isoformat(),
    "run_names": began("a_run_with_no_stamp", naive).isoformat(),
    "timeline": _moment(naive, "from").isoformat(),
    "rule_parameter": rule_instant({"at": naive}, "at"),
    "content_clock": nanoseconds_since_epoch(dt.datetime(2026, 1, 2, 3, 4, 5)),
    "run_label": run_label(1, dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)),
}))
"""

_UTC = "2026-01-02T03:04:05+00:00"
_NS = int(dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC).timestamp()) * 1_000_000_000


@pytest.mark.skipif(sys.platform == "win32", reason="the TZ variable sets no zone on Windows")
@pytest.mark.parametrize(
    ("zone", "label"),
    [
        ("Asia/Tokyo", "Scan 1, January 2, 12:04 PM JST"),
        ("America/New_York", "Scan 1, January 1, 10:04 PM EST"),
    ],
)
def test_an_unzoned_time_is_utc_and_a_run_is_named_in_the_machine_s_zone(
    zone: str, label: str
) -> None:
    done = subprocess.run(
        [sys.executable, "-c", _READ_IN_ZONE],
        env={**os.environ, "TZ": zone, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    said = json.loads(done.stdout.strip().splitlines()[-1])
    for reader in ("rerun", "restore", "changes", "run_names", "timeline"):
        assert said[reader] == _UTC, reader
    assert said["rule_parameter"] == _NS
    assert said["content_clock"] == _NS
    # Converted only here, to name a run for a person: the machine's own zone, said.
    assert said["run_label"] == label
