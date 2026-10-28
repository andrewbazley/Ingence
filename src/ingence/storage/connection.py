"""Every DuckDB connection the engine opens, opened here: nothing is fetched, nothing loads itself.

The engine runs only on the user's machine. DuckDB installs a "known extension" from its own server
the first time a query or a setting needs one that is not on the machine
(``autoinstall_known_extensions``, on by default). Every connection is opened here with automatic
installation off, and a test holds every module to this function.

Nothing loads itself either, and nothing is read from the person's own DuckDB folder. With
installation off, an extension already in ``~/.duckdb/extensions`` still loaded itself the first
time a query needed it (``autoload_known_extensions``). So what the engine ran depended on what any
other DuckDB had once installed on the machine. Automatic loading is off, and the connection reads
extensions from a folder of its own (:data:`EXTENSION_DIRECTORY`), which the engine never writes
and so stays empty. What DuckDB's own build carries (Parquet, JSON, the time-zone support) is part
of the build and loads as usual.

A time zone given in the settings when a connection opens would fetch an extension. DuckDB reads
that zone before the build's own time-zone support is registered, so it looks for the support among
installed extensions, finds none and installs it from its server. The Graph workspace therefore
sets the zone after the connection opens (``api/v1/explorer/projection.py``), where the build's own
support answers. A zone given at opening fails here in words and fetches nothing.

Nothing spills beside wherever the process started. An in-memory connection spills a query larger
than its memory limit to ``temp_directory``, which is ``.tmp`` relative to the working directory by
default. A Dashboard or a command started inside a root would write there. Ingence never writes
inside the folders it reads. The connection spills
into a folder of the system's own temporary directory instead.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import duckdb

__all__ = ["EXTENSION_DIRECTORY", "SPILL_DIRECTORY", "connect"]

#: Where a connection spills a query larger than its memory: the system's temporary folder.
SPILL_DIRECTORY = Path(tempfile.gettempdir()) / "ingence-duckdb"

#: Where a connection looks for extensions: a folder of the engine's own that nothing writes
#: (DuckDB creates it only to install, and the engine installs nothing), never ``~/.duckdb``.
EXTENSION_DIRECTORY = SPILL_DIRECTORY / "no-extensions"


def connect(database: str = ":memory:", **config: Any) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection that installs and loads nothing: ``config`` adds to the settings."""
    settings = {
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "extension_directory": str(EXTENSION_DIRECTORY),
        "temp_directory": str(SPILL_DIRECTORY),
        **config,
    }
    return duckdb.connect(database, config=settings)
