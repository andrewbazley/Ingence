"""Stamp the commit a package was built from into the package.

A run record and ``/api/v1/version`` name the engine's commit, because one package version spans
many commits and feedback has to say which build it came from. A wheel is not a checkout, so the
commit has to be written into it when it is built: ``ingence/_build_commit.txt``, which
``ingence.core.provenance`` reads.

This is a hatchling build hook (``[tool.hatch.build.hooks.custom]`` in ``pyproject.toml``). It
runs in two places, and says the same commit in both:

- building from a checkout (the sdist, or a wheel straight from it): ``git rev-parse HEAD`` of the
  folder being built, with ``+dirty`` where a tracked file differs. The stamp is added to the
  distribution without being written into the checkout, so the checkout stays clean.
- building a wheel from the sdist (what ``python -m build`` does): there is no git there, and the
  sdist already holds the stamp, which is kept as it is.

Where there is no checkout and no stamp, nothing is written and the engine reports no commit. Two
builds of one commit give the same stamp, so the build stays reproducible.

Standard library only: it runs inside the build's own isolated environment.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from hatchling.builders.hooks.plugin.interface import (  # type: ignore[import-not-found, unused-ignore]
        BuildHookInterface,
    )
except ImportError:  # imported outside a build (the tests read ``commit_of``): nothing to extend
    BuildHookInterface = object  # type: ignore[assignment, misc, unused-ignore]

#: Where the stamp lies in a source tree and in a source distribution, and where the wheel puts it.
STAMP_IN_TREE = Path("src") / "ingence" / "_build_commit.txt"
STAMP_IN_WHEEL = "ingence/_build_commit.txt"
_GIT_SECONDS = 20.0


def commit_of(root: Path) -> str | None:
    """The commit of the checkout at ``root``, or None where ``root`` is not a checkout.

    Only a folder with a ``.git`` of its own counts: a source distribution unpacked inside some
    other repository must not take that repository's commit. ``+dirty`` marks tracked changes.
    """
    git = shutil.which("git")
    if git is None or not (root / ".git").exists():
        return None
    try:
        head = subprocess.run(  # noqa: S603 -- git, fixed arguments, on the folder being built
            [git, "--no-optional-locks", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_SECONDS,
            check=False,
        )
        commit = head.stdout.strip()
        if head.returncode != 0 or not commit:
            return None
        changed = subprocess.run(  # noqa: S603
            [
                git,
                "--no-optional-locks",
                "-C",
                str(root),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    dirty = changed.returncode == 0 and bool(changed.stdout.strip())
    return f"{commit}+dirty" if dirty else commit


class StampCommit(BuildHookInterface):  # type: ignore[misc, unused-ignore]
    """Add ``_build_commit.txt`` to the sdist and the wheel when building from a checkout."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable" or self.target_name not in {"sdist", "wheel"}:
            return
        commit = commit_of(Path(self.root))
        if commit is None:
            return
        folder = Path(tempfile.mkdtemp(prefix="ingence-stamp-"))
        stamp = folder / "_build_commit.txt"
        stamp.write_text(commit + "\n", encoding="utf-8")
        destination = STAMP_IN_TREE.as_posix() if self.target_name == "sdist" else STAMP_IN_WHEEL
        build_data["force_include"][str(stamp)] = destination
        self._folder = folder

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        folder = getattr(self, "_folder", None)
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
