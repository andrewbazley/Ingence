"""Every DuckDB connection the engine opens fetches nothing from a network.

DuckDB installs a "known extension" from its own server the first time a query needs one that is not
on the machine (autoinstall). A time-zone extension is one such. The engine opens every connection
through one function, with automatic installation off, so what is not already on the machine fails
to load in words rather than being fetched. A connection opened any other way is a finding.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "ingence"
#: The one module allowed to call ``duckdb.connect`` itself.
THE_ONE = SOURCE / "storage" / "connection.py"


def test_every_engine_connection_opens_through_the_one_that_installs_nothing() -> None:
    bare = [
        f"{path.relative_to(SOURCE)}:{number}"
        for path in sorted(SOURCE.rglob("*.py"))
        if path != THE_ONE
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\bduckdb\.connect\(", line)
    ]
    assert bare == [], bare


def test_a_connection_so_opened_installs_no_extension() -> None:
    from ingence.storage.connection import connect

    with connect() as connection:
        row = connection.execute(
            "SELECT current_setting('autoinstall_known_extensions')"
        ).fetchone()
    assert row is not None and row[0] is False


def test_a_connection_so_opened_spills_nowhere_near_where_the_process_started() -> None:
    """An in-memory connection spills a large query to ``.tmp`` in the working directory by
    default, and a Dashboard started inside a root would write there."""
    import tempfile

    from ingence.storage.connection import connect

    with connect() as connection:
        row = connection.execute("SELECT current_setting('temp_directory')").fetchone()
    assert row is not None
    spill = Path(str(row[0]))
    assert spill.is_absolute()
    assert Path(tempfile.gettempdir()).resolve() in spill.resolve().parents


def test_a_connection_so_opened_loads_no_extension_by_itself() -> None:
    """Installing nothing is not enough. An extension already on the machine loads itself the first
    time a query needs it, from whatever folder DuckDB reads by default."""
    from ingence.storage.connection import connect

    with connect() as connection:
        row = connection.execute("SELECT current_setting('autoload_known_extensions')").fetchone()
    assert row is not None and row[0] is False


def test_a_connection_so_opened_reads_extensions_from_an_empty_folder_of_its_own() -> None:
    """Never the person's ``~/.duckdb``, where any other DuckDB on the machine installs; the
    engine's own folder is never written, so it stays empty."""
    from ingence.storage.connection import EXTENSION_DIRECTORY, connect

    with connect() as connection:
        row = connection.execute("SELECT current_setting('extension_directory')").fetchone()
    assert row is not None
    folder = Path(str(row[0]))
    assert folder == EXTENSION_DIRECTORY
    assert folder.is_absolute()
    assert Path.home() / ".duckdb" not in (folder, *folder.parents)
    assert not folder.exists() or not any(folder.iterdir())


def _plant_extensions(home: Path) -> Path:
    """A home of the test's own holding two extensions where DuckDB installs by default: bytes
    that are no extension, so loading either fails in words that name the file."""
    import duckdb

    with duckdb.connect(config={"autoinstall_known_extensions": False}) as probe:
        row = probe.execute("SELECT * FROM pragma_platform()").fetchone()
    assert row is not None
    folder = home / ".duckdb" / "extensions" / f"v{duckdb.__version__}" / str(row[0])
    folder.mkdir(parents=True)
    for name in ("httpfs", "icu"):
        (folder / f"{name}.duckdb_extension").write_bytes(b"planted by a test, not an extension")
    return folder


#: A query that needs the web extension: a file named by an address on this machine's own
#: loopback, on the discard port, so nothing could leave the machine even if it were read.
_NEEDS_AN_EXTENSION = "SELECT * FROM read_csv('http://127.0.0.1:9/planted.csv')"


def test_an_extension_in_the_person_s_duckdb_folder_is_never_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extension in ``~/.duckdb`` (a downloaded time-zone extension is one) is never loaded
    by the engine, whatever a query or a setting needs."""
    import duckdb

    from ingence.storage.connection import connect

    planted = _plant_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    # The folder a bare connection reads: the planted files are found and tried there.
    with (
        duckdb.connect(config={"autoinstall_known_extensions": False}) as bare,
        pytest.raises(duckdb.Error) as tried,
    ):
        bare.execute(_NEEDS_AN_EXTENSION)
    assert str(planted) in str(tried.value)
    with connect() as connection:
        with pytest.raises(duckdb.Error) as refused:
            connection.execute(_NEEDS_AN_EXTENSION)
        assert str(tmp_path) not in str(refused.value)
        # The zone is set through the time-zone support built into DuckDB, never the planted one.
        connection.execute("SET TimeZone = 'UTC'")
        loaded = connection.execute(
            "SELECT extension_name, install_path FROM duckdb_extensions() WHERE loaded"
        ).fetchall()
    assert all(str(tmp_path) not in str(path or "") for _, path in loaded), loaded
    assert "httpfs" not in {name for name, _ in loaded}


#: Stored earliest credible times as the projection holds them: with a zone, with an offset each
#: side of UTC, naive (UTC by the engine's rule), across a daylight-saving change, and not a time.
_STORED_TIMES = (
    "2023-04-20T09:00:00Z",
    "2023-04-20T09:00:00+02:00",
    "2023-04-20T09:00:00-05:00",
    "2023-04-20T23:30:00",
    "2026-03-08T07:30:00Z",
    "not a time",
    None,
)

#: One fresh interpreter per machine zone (DuckDB takes its zone from the host when a connection
#: opens): the Timeline's and Arrange's casts, through the Graph workspace's connection and
#: through a connection that never sets a zone, over the stored times above.
_IN_ZONE = """
import json, sys
from ingence.api.v1.explorer.arrange import _BEST
from ingence.api.v1.explorer.projection import connect
from ingence.api.v1.explorer.timeline import _TIME_SQL
from ingence.storage.connection import connect as bare

stored = json.loads(sys.argv[1])
rows = ", ".join("(?, NULL::BIGINT, NULL::VARCHAR, NULL::BIGINT)" for _ in stored)
table = f"(VALUES {rows}) AS f(earliest_at, event_time_ns, event_time_source, modified_ns)"
said = {}
for name, opened in (("workspace", connect), ("zone_unset", bare)):
    with opened() as connection:
        said[name] = {
            label: [
                None if value is None else value.isoformat()
                for (value,) in connection.execute(
                    f"SELECT {expression} FROM {table}", stored
                ).fetchall()
            ]
            for label, expression in (("timeline", _TIME_SQL["earliest"]), ("arrange", _BEST))
        }
print(json.dumps(said))
"""


def _utc_reading(text: str | None) -> str | None:
    """The instant a stored time names, as UTC without a zone: what DuckDB's core gives with no
    time-zone support at all (a naive time is UTC; an offset is applied)."""
    import datetime as dt

    if text is None:
        return None
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(dt.UTC).replace(tzinfo=None)
    return moment.isoformat()


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="Windows gives DuckDB its own zone setting, not the TZ variable",
)
def test_the_graph_workspace_s_times_are_the_same_with_or_without_time_zone_support() -> None:
    """The Timeline's and Arrange's casts through ``TIMESTAMPTZ`` give, under two machine zones,
    exactly the UTC instants DuckDB's core gives without its time-zone extension, so a machine with
    the extension and one without place every file alike. The same casts on a connection that sets
    no zone follow the machine's clock, which is why the zone is set."""
    import json
    import os
    import subprocess
    import sys

    expected = [_utc_reading(text) for text in _STORED_TIMES]
    followed_the_clock = []
    for zone in ("Asia/Tokyo", "America/New_York"):
        done = subprocess.run(
            [sys.executable, "-c", _IN_ZONE, json.dumps(_STORED_TIMES)],
            env={**os.environ, "TZ": zone, "PYTHONPATH": os.pathsep.join(sys.path)},
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        said = json.loads(done.stdout.strip().splitlines()[-1])
        assert said["workspace"]["timeline"] == expected, zone
        assert said["workspace"]["arrange"] == expected, zone
        followed_the_clock.append(said["zone_unset"]["timeline"] != expected)
    assert all(followed_the_clock)
