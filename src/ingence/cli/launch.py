"""The ``ingence`` console script's entry point, which imports the command line only when called.

Every child that the supervisor spawns re-runs its parent's main module before it does any work.
Started as the console script, that module is the launcher that the installer writes, and its top
level is one import: the entry point that ``pyproject.toml`` names. Whatever the module holding that
entry point imports at module scope, every isolated extraction imports again before it reads a byte.
From ``ingence.cli.main``, that import was Typer, Rich, Pydantic, DuckDB and PyArrow. It measured
0.585 s in a fresh interpreter and added 0.513 s to each isolated file of a run on real laboratory
folders.

So this module imports nothing but ``sys`` (loaded before any interpreter runs a line). The command
line is imported inside :func:`main`, which a child never calls. The launcher calls it only under
``if __name__ == "__main__"``, and a child re-runs the launcher under another name.

``ingence --version`` is answered here, before the command line is imported. The first run after an
install compiles every module it imports, and importing the command line imports the whole engine.
That gives 10 to 15 seconds of silence for two strings that the package's own ``__init__`` holds. So
a command line that is ``--version`` and nothing else prints them from there, in the words that the
command line's own option prints. Every other command line imports the command line as usual.
"""

from __future__ import annotations

import sys

__all__ = ["main"]

#: The one command line answered without importing the command line.
_VERSION_ONLY = ["--version"]


def main() -> None:
    """Run the command line, imported here rather than at module scope."""
    if sys.argv[1:] == _VERSION_ONLY:
        from pathlib import Path

        import ingence
        from ingence import SCHEMA_VERSION, __version__

        # A built package names its commit from the stamp that the build wrote beside ``__init__``
        # (``ingence.core.provenance`` reads the same file). A checkout has none, and its commit is
        # what ``ingence.cli.main``'s own option prints.
        stamp = Path(ingence.__file__).with_name("_build_commit.txt")
        try:
            commit = stamp.read_text(encoding="utf-8").strip()
        except OSError:
            commit = ""
        build = f", build {commit[:12]}" if commit else ""
        print(f"ingence {__version__} (schema {SCHEMA_VERSION}{build})")
        return
    from ingence.cli.main import main as command_line

    command_line()
