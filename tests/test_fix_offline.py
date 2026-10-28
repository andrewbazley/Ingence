"""``ingence fix`` works offline.

The class: a diagnosis of the project reads the project, so a declared root it cannot reach
now (an unmounted share, a disk not plugged in) is said as unchecked, "not reachable now; not
checked", and every other check still runs and the command ends normally; an unreachable root is
not a broken project.

The root is declared at a path under ``/Volumes/`` that no machine mounts, and every ``stat``,
open or listing of a path under ``/Volumes/`` is refused by a guard the way a share that has gone
away refuses one, so nothing here can reach a real share either.
Every name here is invented.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import secrets
from contextlib import contextmanager
from typing import TYPE_CHECKING

import yaml
from typer.testing import CliRunner

from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.schema import SCHEMA_VERSION

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

#: Where no share is mounted on any machine the tests run on.
GUARDED = "/Volumes/"


@contextmanager
def _refusing(prefix: str) -> Iterator[list[str]]:
    """Refuse every ``stat``, open, listing or access of a path under ``prefix``, as a share that
    has gone away refuses one, and record each attempt."""
    attempts: list[str] = []
    names = ("stat", "lstat", "scandir", "listdir", "open", "access", "readlink")
    real = {name: getattr(os, name) for name in names}
    real_open = builtins.open

    def refusing(name: str, function):  # type: ignore[no-untyped-def]
        def guarded(*args, **kwargs):  # type: ignore[no-untyped-def]
            target = args[0] if args else kwargs.get("path")
            if isinstance(target, (str, bytes, os.PathLike)):
                text = os.fsdecode(target)
                if text.startswith(prefix):
                    attempts.append(f"{name} {text}")
                    raise PermissionError(13, "refused by the test's guard", text)
            return function(*args, **kwargs)

        return guarded

    for name in names:
        setattr(os, name, refusing(f"os.{name}", real[name]))
    builtins.open = refusing("open", real_open)
    io.open = builtins.open
    try:
        yield attempts
    finally:
        for name in names:
            setattr(os, name, real[name])
        builtins.open = real_open
        io.open = real_open


def _offline_project(tmp_path: Path) -> tuple[Path, str]:
    """A project whose one root is declared under ``/Volumes/`` at a share nobody mounts, with a
    former default an older ``init`` wrote into its settings."""
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    absent = f"{GUARDED}zz-not-mounted-{secrets.token_hex(4)}/data"
    identity_file = project.path / "project.json"
    identity = json.loads(identity_file.read_text())
    identity["roots"][0]["mount_path"] = absent
    identity_file.write_text(json.dumps(identity, indent=2))
    (project.path / "settings.yaml").write_text(
        yaml.safe_dump({"jobs": {"stop_silent": False}, "schema_version": SCHEMA_VERSION})
    )
    return project.path, absent


def test_fix_names_a_root_it_cannot_reach_unchecked_and_goes_on(tmp_path: Path) -> None:
    project, absent = _offline_project(tmp_path)
    with _refusing(GUARDED) as attempts:
        result = CliRunner().invoke(app, ["fix", "-p", str(project)])
    said = " ".join(result.output.split())
    assert result.exit_code == 0, result.output
    assert absent in said
    assert "cannot be reached now; not checked" in said
    assert "missing" not in said
    # The other checks still ran: the former default an older init wrote is named.
    assert "jobs.stop_silent: False" in said
    # The root's own entry was asked about once at most, and nothing beneath it.
    assert all(attempt.endswith(absent) for attempt in attempts), attempts


def test_a_root_simply_absent_is_unchecked_too(tmp_path: Path) -> None:
    """Without the guard, the path does not exist at all: the same words, the same normal end."""
    project, absent = _offline_project(tmp_path)
    identity_file = project / "project.json"
    identity = json.loads(identity_file.read_text())
    gone = tmp_path / "unplugged" / "data"
    identity["roots"][0]["mount_path"] = str(gone)
    identity_file.write_text(json.dumps(identity, indent=2))
    result = CliRunner().invoke(app, ["fix", "-p", str(project)])
    said = " ".join(result.output.split())
    assert result.exit_code == 0, result.output
    assert f"{gone}: cannot be reached now; not checked" in said
    del absent


def test_a_root_that_is_a_file_is_still_a_problem(tmp_path: Path) -> None:
    """Reachable and wrong is not offline: a declared root that is a file is said as a problem."""
    project, _ = _offline_project(tmp_path)
    identity_file = project / "project.json"
    identity = json.loads(identity_file.read_text())
    not_a_folder = tmp_path / "brisa.txt"
    not_a_folder.write_text("vorn")
    identity["roots"][0]["mount_path"] = str(not_a_folder)
    identity_file.write_text(json.dumps(identity, indent=2))
    result = CliRunner().invoke(app, ["fix", "-p", str(project)])
    assert result.exit_code == 1, result.output
    assert "not a folder" in " ".join(result.output.split())
