"""Which engine wrote a run: its commit, read once per process.

A run record states the package version and the schema. A development build moves many commits
between one package version and the next, so two runs "by 0.1.0.dev0" can come from engines that
read the same files differently. The commit identifies an engine.

The commit is read from these sources, in order:

1. ``INGENCE_ENGINE_COMMIT`` in the environment, which a runner or a packaging step sets where
   the engine is not a checkout (a snapshot, an installed wheel).
2. The build's own stamp, ``ingence/_build_commit.txt``, where a build wrote one.
3. The git checkout the package is imported from, with ``+dirty`` where tracked files differ.

The result is ``None`` where none of them gives a commit. Reading the checkout runs ``git`` on the
engine's own source, never on a root, and never over a network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import cache
from pathlib import Path

__all__ = ["BUILD_STAMP", "ENVIRONMENT_VARIABLE", "engine_commit"]

ENVIRONMENT_VARIABLE = "INGENCE_ENGINE_COMMIT"
#: Written into the package by a build that is not a checkout. Read before git.
BUILD_STAMP = Path(__file__).resolve().parent.parent / "_build_commit.txt"

#: A commit read from git takes a few milliseconds. A hung git must not hang a run.
_GIT_SECONDS = 5.0


@cache
def engine_commit() -> str | None:
    """The engine's commit, as the module docstring orders the sources, or ``None``."""
    stated = os.environ.get(ENVIRONMENT_VARIABLE, "").strip()
    if stated:
        return stated
    try:
        stamped = BUILD_STAMP.read_text(encoding="utf-8").strip()
    except OSError:
        stamped = ""
    if stamped:
        return stamped
    return _from_checkout(Path(__file__).resolve().parent.parent)


def _from_checkout(source: Path) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        head = subprocess.run(  # noqa: S603 -- git on the engine's own source, fixed arguments
            [git, "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_SECONDS,
            check=False,
        )
        if head.returncode != 0:
            return None
        commit = head.stdout.strip()
        changed = subprocess.run(  # noqa: S603
            [git, "-C", str(source), "status", "--porcelain", "--untracked-files=no", "--", "."],
            capture_output=True,
            text=True,
            timeout=_GIT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not commit:
        return None
    return f"{commit}+dirty" if changed.returncode == 0 and changed.stdout.strip() else commit
